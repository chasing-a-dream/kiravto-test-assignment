"""Import an observed partial snapshot without replacing DB or moderation history."""
import json
import sys
from kiravto.domain import Store
from kiravto.observability import record

snapshot=json.load(open(sys.argv[1],encoding='utf-8'))
store=Store(sys.argv[2])
try:
    metrics=snapshot['metrics']
    # A copied snapshot can never establish source completeness or removal.
    exists=store.db.execute('SELECT id FROM runs WHERE observed_at=?',(metrics['observed_at'],)).fetchone()
    if exists:
        print(json.dumps({'already_imported':exists['id']}))
    else:
        run=store.ingest(snapshot['offers'],complete=False,expected_count=metrics['expected_count'],now=metrics['observed_at'])
        record(sys.argv[2],{**metrics,**run,'imported_observation':True})
        print(json.dumps({'imported_run':run['id'],'count':run['count'],'complete':False}))
finally:store.close()
