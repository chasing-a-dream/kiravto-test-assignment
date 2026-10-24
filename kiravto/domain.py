"""SQLite domain model. Input amounts are integer roubles; timestamps are ISO 8601."""
import json
import re
import sqlite3


def full_vin(value):
    value = str(value or '').strip().upper()
    return value if re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', value) else None


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, requested_complete INTEGER,
          complete INTEGER NOT NULL, expected_count INTEGER, count INTEGER NOT NULL,
          coverage REAL NOT NULL, anomalies TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS offers (
          id TEXT PRIMARY KEY, vin TEXT, status TEXT NOT NULL,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, removed_at TEXT,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations (
          id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
          offer_id TEXT NOT NULL REFERENCES offers(id), observed_at TEXT NOT NULL,
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
          offer_id TEXT NOT NULL REFERENCES offers(id), observed_at TEXT NOT NULL,
          kind TEXT NOT NULL, details TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS observations_offer ON observations(offer_id,id);
        CREATE INDEX IF NOT EXISTS events_offer ON events(offer_id,id);
        CREATE INDEX IF NOT EXISTS offers_vin ON offers(vin);
        CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations
          BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;
        CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations
          BEGIN SELECT RAISE(ABORT, 'immutable observation'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
          BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
          BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
        ''')

    def close(self):
        self.db.close()

    def _event(self, run, identity, now, kind, details):
        self.db.execute('INSERT INTO events(run_id,offer_id,observed_at,kind,details) VALUES(?,?,?,?,?)',
                        (run, identity, now, kind, json.dumps(details, ensure_ascii=False, sort_keys=True)))

    def ingest(self, offers, complete=False, expected_count=None, confirmed_missing=None, now=None,missing_evidence=None):
        """Record a run atomically. Only an adequate complete run can remove confirmed IDs.

        An ID changing with a valid identical VIN produces a relisted event linking IDs;
        it never implicitly removes its predecessor. No sale is inferred from disappearance.
        Search returns latest payloads; history returns immutable observations and events.
        """
        if not now:
            raise ValueError('now ISO timestamp is required')
        from datetime import datetime
        datetime.fromisoformat(now.replace('Z', '+00:00'))
        if expected_count is not None and (not isinstance(expected_count, int) or expected_count < 0):
            raise ValueError('expected_count must be nonnegative integer')
        items, anomalies = {}, []
        for raw in offers:
            raw = dict(raw)
            identity = raw.get('id', raw.get('source_id'))
            if identity is None or str(identity).strip() == '':
                anomalies.append({'type': 'missing_id'})
                continue
            identity = str(identity)
            if identity in items:
                anomalies.append({'type': 'duplicate_id', 'id': identity})
                continue
            raw['id'] = identity
            for key in ('price', 'discount_price'):
                amount = raw.get(key)
                if amount is not None and (type(amount) is not int or amount < 0):
                    raise ValueError(key + ' must be nonnegative integer or None')
            items[identity] = raw
        with self.db:
            previous = self.db.execute('SELECT count FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1').fetchone()
            baseline = previous['count'] if previous else 0
            coverage = len(items) / baseline if baseline else 1.0
            adequate = coverage >= .9 and (expected_count is None or len(items) >= expected_count)
            if coverage < .9:
                anomalies.append({'type': 'low_coverage', 'previous_count': baseline, 'count': len(items)})
            if expected_count is not None and len(items) < expected_count:
                anomalies.append({'type': 'expected_count_mismatch', 'expected_count': expected_count})
            if anomalies:
                adequate = False
            effective = bool(complete and adequate)
            cursor = self.db.execute('INSERT INTO runs(observed_at,requested_complete,complete,expected_count,count,coverage,anomalies) VALUES(?,?,?,?,?,?,?)',
                                     (now, bool(complete), effective, expected_count, len(items), coverage, json.dumps(anomalies)))
            run = cursor.lastrowid
            for identity, raw in items.items():
                encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True)
                vin = full_vin(raw.get('vin'))
                old = self.db.execute('SELECT * FROM offers WHERE id=?', (identity,)).fetchone()
                if old is None:
                    self.db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)', (identity, vin, 'active', now, now, None, encoded))
                    prior = self.db.execute('SELECT id,status FROM offers WHERE vin=? AND id<>? ORDER BY last_seen DESC,id', (vin, identity)).fetchall() if vin else []
                    match_by='full_vin'
                    photo_hash=raw.get('photo_sha256','')
                    if not prior and isinstance(photo_hash,str) and re.fullmatch('[a-f0-9]{64}',photo_hash):
                        prior=self.db.execute("SELECT id,status FROM offers WHERE json_extract(payload,'$.photo_sha256')=? AND id<>? ORDER BY last_seen DESC,id",(photo_hash,identity)).fetchall()
                        match_by='photo_content_sha256'
                    kind='duplicate' if any(r['status']=='active' for r in prior) else ('relisted' if prior else 'new')
                    self._event(run, identity, now, kind, {'previous_ids': [r['id'] for r in prior],'matched_by':match_by} if prior else {})
                else:
                    old_payload = json.loads(old['payload'])
                    if old['status'] in ('removed','probably_sold'):
                        self._event(run, identity, now, 'returned', {})
                    for key in sorted(set(old_payload) | set(raw)):
                        if old_payload.get(key) != raw.get(key):
                            self._event(run, identity, now, key + '_changed', {'before': old_payload.get(key), 'after': raw.get(key)})
                    self.db.execute('UPDATE offers SET vin=?,status=?,last_seen=?,removed_at=NULL,payload=? WHERE id=?', (vin, 'active', now, encoded, identity))
                self.db.execute('INSERT INTO observations(run_id,offer_id,observed_at,payload) VALUES(?,?,?,?)', (run, identity, now, encoded))
            if effective:
                for identity in set(map(str, confirmed_missing or ())) - items.keys():
                    old = self.db.execute('SELECT status FROM offers WHERE id=?', (identity,)).fetchone()
                    if old and old['status'] == 'active':
                        self.db.execute('UPDATE offers SET status=?,removed_at=? WHERE id=?', ('removed', now, identity))
                        evidence=(missing_evidence or {}).get(identity)
                        self._event(run, identity, now, 'removed', {'confirmation': 'confirmed_missing',**({'source_evidence':evidence} if evidence else {})})
        return self.runs(limit=1)[0]

    def classify_probable_sales(self, now, days=7):
        """Infer probable sale only with recent full evidence after the grace period."""
        from datetime import datetime,timedelta
        if not isinstance(days,int) or days<1: raise ValueError('days must be positive')
        current=datetime.fromisoformat(now)
        latest=self.db.execute('SELECT id,observed_at FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1').fetchone()
        if latest is None or not current-timedelta(hours=2)<=datetime.fromisoformat(latest['observed_at'])<=current: return []
        result=[]
        with self.db:
            for row in self.db.execute("SELECT * FROM offers WHERE status='removed'").fetchall():
                threshold=datetime.fromisoformat(row['removed_at'])+timedelta(days=days)
                if datetime.fromisoformat(latest['observed_at'])<threshold: continue
                if row['vin'] and self.db.execute("SELECT 1 FROM offers WHERE vin=? AND status='active'",(row['vin'],)).fetchone(): continue
                self.db.execute("UPDATE offers SET status='probably_sold' WHERE id=?",(row['id'],))
                self._event(latest['id'],row['id'],now,'probably_sold',{'days':days,'warning':'inference, not confirmed sale'})
                result.append(row['id'])
        return result

    @staticmethod
    def _offer(row):
        if row is None:
            return None
        result = json.loads(row['payload'])
        result.update({key: row[key] for key in ('id','status','first_seen','last_seen','removed_at')})
        return result

    def get(self, identity):
        return self._offer(self.db.execute('SELECT * FROM offers WHERE id=?', (str(identity),)).fetchone())

    def search(self, status='active', limit=100, **filters):
        if not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError('limit must be 1..10000')
        rows = self.db.execute('SELECT * FROM offers ORDER BY id').fetchall()
        results = (self._offer(row) for row in rows)
        return [item for item in results if (status is None or item['status'] == status) and
                all(item.get(k) == v for k, v in filters.items())][:limit]

    def history(self, identity):
        observations = [dict(row) for row in self.db.execute('SELECT * FROM observations WHERE offer_id=? ORDER BY id', (str(identity),))]
        events = [dict(row) for row in self.db.execute('SELECT * FROM events WHERE offer_id=? ORDER BY id', (str(identity),))]
        for row in observations:
            row['payload'] = json.loads(row['payload'])
        for row in events:
            row['details'] = json.loads(row['details'])
        return {'observations': observations, 'events': events}

    def drops(self, limit=100):
        rows = self.db.execute("SELECT * FROM events WHERE kind IN ('price_changed','discount_price_changed') ORDER BY id DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['details'] = json.loads(item['details'])
            before, after = item['details']['before'], item['details']['after']
            if before is not None and after is not None and after < before:
                result.append(item)
        return result[:limit]

    def stock(self):
        counts = {r['status']: r['n'] for r in self.db.execute('SELECT status,COUNT(*) n FROM offers GROUP BY status')}
        return {'active': counts.get('active', 0), 'removed': counts.get('removed', 0), 'total': sum(counts.values())}

    def runs(self, limit=100):
        rows = [dict(r) for r in self.db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT ?', (limit,))]
        for row in rows:
            row['anomalies'] = json.loads(row['anomalies'])
            row['complete'] = bool(row['complete'])
            row['requested_complete'] = bool(row['requested_complete'])
        return rows
