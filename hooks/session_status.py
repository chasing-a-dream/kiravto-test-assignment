"""Expose only service status; never read credentials."""
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

DOCKER_QUERY="""import json,sqlite3
db=sqlite3.connect('file:/app/data/stock.sqlite?mode=ro',uri=True)
run=db.execute('SELECT id,observed_at,complete,count FROM runs ORDER BY id DESC LIMIT 1').fetchone()
exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone()
queue=dict(db.execute("SELECT state,count(*) FROM posts WHERE state NOT IN ('sent','rejected') GROUP BY state").fetchall()) if exists else {}
print(json.dumps({'latest_finished_run':run,'moderation_queue':queue}))
"""

def status(root):
    try:
        result=subprocess.run(['docker','exec','kiravto-api-1','python','-c',DOCKER_QUERY],capture_output=True,text=True,encoding='utf-8',timeout=3)
        data=json.loads(result.stdout) if result.returncode==0 else None
        if isinstance(data,dict) and 'latest_finished_run' in data and isinstance(data.get('moderation_queue'),dict):
            return 'Docker service snapshot: '+json.dumps(data)+'. Do not infer removals from incomplete runs.'
    except (OSError,subprocess.TimeoutExpired,ValueError):pass
    path=Path(root)/'data'/'stock.sqlite'
    prefix='Docker service status unavailable. '
    if not path.exists(): return prefix+'No local observations. Full source contract remains unverified.'
    try:
        with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as db:
            run=db.execute('SELECT observed_at,complete,count FROM runs ORDER BY id DESC LIMIT 1').fetchone()
            return prefix+'Development snapshot only: '+str(run)+'. Do not treat it as current Docker state or infer removals.'
    except sqlite3.Error: return prefix+'Local database unavailable; do not claim service health.'

if __name__=='__main__':
    import sys
    event=json.load(sys.stdin)
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':status(event.get('cwd','.'))}}))
