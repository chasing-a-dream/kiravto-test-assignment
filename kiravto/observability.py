import json
import sqlite3
import time
from contextlib import contextmanager

@contextmanager
def connection(path):
    db=sqlite3.connect(str(path),timeout=30)
    db.row_factory=sqlite3.Row
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS run_metrics(run_id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS admin_alerts(id INTEGER PRIMARY KEY,dedupe TEXT UNIQUE NOT NULL,text TEXT NOT NULL,state TEXT NOT NULL DEFAULT \'pending\')')
            if 'retry_at' not in {row[1] for row in db.execute('PRAGMA table_info(admin_alerts)')}:
                db.execute('ALTER TABLE admin_alerts ADD COLUMN retry_at REAL NOT NULL DEFAULT 0')
            yield db
    finally: db.close()

def record(path,metrics,notify=True):
    with connection(path) as db:
        db.execute('INSERT OR REPLACE INTO run_metrics VALUES(?,?)',(metrics['id'],json.dumps(metrics,ensure_ascii=False)))
        last=db.execute('SELECT id,complete FROM runs ORDER BY id DESC LIMIT 2').fetchall()
        if notify and len(last)==2 and all(not row['complete'] for row in last):
            success=db.execute('SELECT id FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1').fetchone()
            baseline=success['id'] if success else 0
            db.execute('INSERT OR IGNORE INTO admin_alerts(dedupe,text) VALUES(?,?)',(f'incomplete-streak:{baseline}','Два неполных прогона подряд. Снятие объявлений заблокировано. Последний прогон #'+str(metrics['id'])))

def with_metrics(path,runs):
    with connection(path) as db:
        data={row['run_id']:json.loads(row['payload']) for row in db.execute('SELECT * FROM run_metrics')}
    return [{**row,**{k:v for k,v in data.get(row['id'],{}).items() if k not in row}} for row in runs]

def recover_alerts(path):
    with connection(path) as db:
        db.execute("UPDATE admin_alerts SET state='uncertain' WHERE state='sending'")

def send_alerts(path,bot,chat):
    """Send operational notices to the configured admin group, never a channel."""
    from kiravto.telegram import BotError
    with connection(path) as db:
        ids=[row['id'] for row in db.execute("SELECT id FROM admin_alerts WHERE state='pending' AND retry_at<=?",(time.time(),))]
    for identity in ids:
        with connection(path) as db:
            claimed=db.execute("UPDATE admin_alerts SET state='sending' WHERE id=? AND state='pending'",(identity,)).rowcount
            row=db.execute('SELECT text FROM admin_alerts WHERE id=?',(identity,)).fetchone()
        if not claimed: continue
        try:
            result=bot.call('sendMessage',{'chat_id':chat,'text':row['text']})
            if not isinstance(result,dict) or not isinstance(result.get('message_id'),int): raise TimeoutError('Unknown result')
            state,retry='sent',0
        except BotError as exc:
            state,retry='pending',time.time()+max(1,exc.retry_after)
        except TimeoutError:
            state,retry='uncertain',0
        with connection(path) as db:
            db.execute('UPDATE admin_alerts SET state=?,retry_at=? WHERE id=?',(state,retry,identity))
