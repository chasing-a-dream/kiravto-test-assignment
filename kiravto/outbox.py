import json
import sqlite3
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

class Outbox:
    def __init__(self, path):
        self.path = str(path)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                created TEXT NOT NULL, moderator INTEGER, error TEXT,
                retry_at REAL NOT NULL DEFAULT 0, message_ids TEXT)''')
            if 'target_channel' not in {row[1] for row in db.execute('PRAGMA table_info(posts)')}:
                db.execute('ALTER TABLE posts ADD COLUMN target_channel TEXT')
                # Pre-migration notifications/approvals were exclusively test.
                db.execute("UPDATE posts SET target_channel='@autostock_demo_test'")
            if 'approval_key' not in {row[1] for row in db.execute('PRAGMA table_info(posts)')}:
                db.execute('ALTER TABLE posts ADD COLUMN approval_key TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS moderation_notifications(post_id INTEGER PRIMARY KEY,message_id INTEGER,state TEXT NOT NULL,target_channel TEXT,callback_key TEXT)')
            columns={row[1] for row in db.execute('PRAGMA table_info(moderation_notifications)')}
            for name in ('target_channel','callback_key'):
                if name not in columns:db.execute('ALTER TABLE moderation_notifications ADD COLUMN '+name+' TEXT')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def propose(self, dedupe, payload):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO posts(dedupe,payload,created) VALUES(?,?,?)', (dedupe,json.dumps(payload,ensure_ascii=False),now()))
            row = db.execute('SELECT id,state FROM posts WHERE dedupe=?',(dedupe,)).fetchone()
            return dict(row)

    def queue(self):
        with self.db() as db:
            return [dict(x) for x in db.execute("SELECT * FROM posts WHERE state NOT IN ('sent','rejected') ORDER BY id")]

    def bind_target(self,post_id,channel):
        from kiravto.config import TEST_CHANNEL,PRODUCTION_CHANNEL
        if channel not in (TEST_CHANNEL,PRODUCTION_CHANNEL):raise ValueError('Unknown moderation target')
        with self.db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='moderation_notifications'").fetchone():
                # Also cover notifications created by an old, still-running
                # test worker after another process migrated the schema.
                db.execute("UPDATE posts SET target_channel=? WHERE id=? AND target_channel IS NULL AND EXISTS(SELECT 1 FROM moderation_notifications WHERE post_id=posts.id)",(TEST_CHANNEL,post_id))
            db.execute("UPDATE posts SET target_channel=? WHERE id=? AND state='pending' AND target_channel IS NULL",(channel,post_id))
            row=db.execute('SELECT target_channel FROM posts WHERE id=?',(post_id,)).fetchone()
            return bool(row and row['target_channel']==channel)

    def reserve_moderation(self,post_id,channel):
        """Reserve notification and bind its channel in one SQLite transaction."""
        from kiravto.config import TEST_CHANNEL,PRODUCTION_CHANNEL
        if channel not in (TEST_CHANNEL,PRODUCTION_CHANNEL):raise ValueError('Unknown moderation target')
        with self.db() as db:
            # Acquire the write lock before inspecting legacy notification rows.
            db.execute("UPDATE posts SET target_channel=target_channel WHERE id=?",(post_id,))
            row=db.execute('SELECT state,target_channel FROM posts WHERE id=?',(post_id,)).fetchone()
            old=db.execute('SELECT * FROM moderation_notifications WHERE post_id=?',(post_id,)).fetchone()
            if old:
                if not old['callback_key']:
                    db.execute("UPDATE posts SET target_channel=? WHERE id=? AND target_channel IS NULL",(TEST_CHANNEL,post_id))
                return None
            if not row or row['state']!='pending' or row['target_channel'] not in (None,channel):return None
            key=secrets.token_urlsafe(9)
            db.execute("INSERT INTO moderation_notifications(post_id,state,target_channel,callback_key) VALUES(?,'sending',?,?)",(post_id,channel,key))
            db.execute('UPDATE posts SET target_channel=? WHERE id=?',(channel,post_id))
            return key

    def decide(self, post_id, publish, chat_id, user_id, admin_chat_id, moderators,channel='@autostock_demo_test',callback_key=None,message_id=None):
        if str(chat_id) != str(admin_chat_id) or str(user_id) not in {str(x) for x in moderators}:
            raise PermissionError('Only configured moderators in the admin chat can decide')
        with self.db() as db:
            notification=db.execute('SELECT * FROM moderation_notifications WHERE post_id=?',(post_id,)).fetchone()
            if publish:
                if notification and notification['callback_key']:
                    if notification['target_channel']!=channel or notification['state']!='sent' or not isinstance(callback_key,str) or not secrets.compare_digest(callback_key,notification['callback_key']) or message_id!=notification['message_id']:
                        return False
                elif channel!='@autostock_demo_test':return False
            # Legacy/unbound proposals belong to test. A changed runtime mode
            # never turns their existing button into production approval.
            state=('approved_bound' if channel!='@autostock_demo_test' else 'approved') if publish else 'rejected'
            return db.execute("UPDATE posts SET state=?,moderator=?,approval_key=?,target_channel=COALESCE(target_channel,'@autostock_demo_test') WHERE id=? AND state='pending' AND (?=0 OR COALESCE(target_channel,'@autostock_demo_test')=?)", (state,user_id,callback_key if publish else None,post_id,bool(publish),channel)).rowcount == 1

    def claim(self, post_id,channel=None):
        with self.db() as db:
            changed = db.execute("UPDATE posts SET state='sending' WHERE id=? AND state IN ('approved','approved_bound') AND (? IS NULL OR COALESCE(target_channel,'@autostock_demo_test')=?) AND (COALESCE(target_channel,'@autostock_demo_test')!='@autostock_demo_main' OR (state='approved_bound' AND approval_key IS NOT NULL))",(post_id,channel,channel)).rowcount
            return dict(db.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()) if changed else None

    def retry_failed(self,post_id):
        """Explicit repair of a rejected send, retaining the original human approval.

        Never retries uncertain delivery and never approves a pending proposal.
        """
        with self.db() as db:
            return db.execute("UPDATE posts SET state=CASE WHEN target_channel='@autostock_demo_main' THEN 'approved_bound' ELSE 'approved' END,error=NULL,retry_at=0 WHERE id=? AND state='failed' AND moderator IS NOT NULL AND (COALESCE(target_channel,'@autostock_demo_test')!='@autostock_demo_main' OR approval_key IS NOT NULL)",(post_id,)).rowcount==1

    def finish(self, post_id, state, error=None, retry_at=0, message_ids=None):
        if state not in ('sent','approved','uncertain','failed'):
            raise ValueError('Invalid delivery state')
        with self.db() as db:
            if state=='approved':
                row=db.execute('SELECT target_channel FROM posts WHERE id=?',(post_id,)).fetchone()
                if row and row['target_channel']=='@autostock_demo_main':state='approved_bound'
            db.execute("UPDATE posts SET state=?,error=?,retry_at=?,message_ids=? WHERE id=? AND state='sending'",(state,error,retry_at,json.dumps(message_ids),post_id))

    def recover(self):
        # A crash after Telegram accepted a request cannot be retried blindly.
        with self.db() as db:
            db.execute("UPDATE posts SET state='uncertain',error='Interrupted send: manual reconciliation required' WHERE state='sending'")
