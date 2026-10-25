"""Shared data-health snapshot for the HTTP API and moderator bot."""
from contextlib import closing
from datetime import datetime, timezone
from kiravto.domain import Store
from kiravto.observability import with_metrics


def snapshot(path, now=None):
    current=now or datetime.now(timezone.utc)
    with closing(Store(path)) as store:
        runs=with_metrics(path,store.runs(limit=1))
        full=store.db.execute('SELECT * FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1').fetchone()
        full=with_metrics(path,[dict(full)])[0] if full else None
    last=full['observed_at'] if full else None
    fresh=False
    if last:
        try:
            stamp=datetime.fromisoformat(last.replace('Z','+00:00'))
            if stamp.utcoffset() is not None:
                fresh=0<=(current-stamp).total_seconds()<=7200
        except (ValueError,TypeError,AttributeError):
            pass
    latest=runs[0] if runs else None
    verified=bool(latest and latest['complete'] and not latest.get('limitations'))
    return {'status':'ok' if verified and fresh else 'degraded',
            'last_successful_run':last,'stale':not fresh,'latest_run':latest,
            'last_complete_run':full,'source_contract_complete':verified}
