import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import httpx
from kiravto.config import Settings,delivery_channel,TEST_CHANNEL,PRODUCTION_CHANNEL
from kiravto.outbox import Outbox
from kiravto.telegram import Bot

class DeliveryModeTests(unittest.TestCase):
    def test_mode_requires_two_explicit_settings_and_known_channel(self):
        self.assertEqual(delivery_channel('test',False),TEST_CHANNEL)
        self.assertEqual(delivery_channel('test',True),TEST_CHANNEL)
        self.assertEqual(delivery_channel('production',True),PRODUCTION_CHANNEL)
        for mode,enabled,target in [('production',False,PRODUCTION_CHANNEL),('production',True,'@other'),('unknown',True,PRODUCTION_CHANNEL)]:
            with self.subTest(mode=mode,enabled=enabled,target=target),self.assertRaises(RuntimeError):
                delivery_channel(mode,enabled,production_channel=target)
        with self.assertRaises(RuntimeError):Settings(channel=PRODUCTION_CHANNEL)

    def test_old_approval_cannot_be_redirected_and_no_network_attempt_occurs(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('old',{'text':'test approval'})['id']
            box.decide(post,True,1,2,1,[2]);calls=[]
            def fake(request):calls.append(request);return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
            bot=Bot(Settings(bot_token='fake-only',mode='production',production_enabled=True,channel=PRODUCTION_CHANNEL),httpx.MockTransport(fake))
            try:self.assertFalse(bot.deliver(box,post))
            finally:bot.close()
            self.assertEqual(calls,[])
            self.assertEqual(box.queue()[0]['state'],'approved')

    def test_production_proposal_still_requires_moderator_and_bound_channel(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('new',{'text':'approved fake example'})['id']
            key=box.reserve_moderation(post,PRODUCTION_CHANNEL)
            self.assertTrue(key)
            with box.db() as db:db.execute("UPDATE moderation_notifications SET state='sent',message_id=123 WHERE post_id=?",(post,))
            self.assertFalse(box.bind_target(post,TEST_CHANNEL))
            self.assertFalse(box.decide(post,True,1,2,1,[2],channel=TEST_CHANNEL))
            calls=[]
            def fake(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
            bot=Bot(Settings(bot_token='fake-only',mode='production',production_enabled=True,channel=PRODUCTION_CHANNEL),httpx.MockTransport(fake))
            try:
                self.assertFalse(bot.deliver(box,post))
                self.assertEqual(calls,[])
                self.assertTrue(box.decide(post,True,1,2,1,[2],channel=PRODUCTION_CHANNEL,callback_key=key,message_id=123))
                self.assertTrue(bot.deliver(box,post))
                self.assertFalse(bot.deliver(box,post))
            finally:bot.close()
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]['chat_id'],PRODUCTION_CHANNEL)

    def test_legacy_pending_migration_cannot_be_rebound_to_production(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'old.db';db=sqlite3.connect(path)
            db.execute("CREATE TABLE posts(id INTEGER PRIMARY KEY,dedupe TEXT UNIQUE,payload TEXT,state TEXT,created TEXT,moderator INTEGER,error TEXT,retry_at REAL DEFAULT 0,message_ids TEXT)")
            db.execute("INSERT INTO posts(id,dedupe,payload,state) VALUES(1,'legacy','{}','pending')");db.commit();db.close()
            box=Outbox(path)
            self.assertEqual(box.queue()[0]['target_channel'],TEST_CHANNEL)
            self.assertFalse(box.bind_target(1,PRODUCTION_CHANNEL))
            self.assertFalse(box.decide(1,True,1,2,1,[2],channel=PRODUCTION_CHANNEL))
            self.assertTrue(box.decide(1,False,1,2,1,[2],channel=PRODUCTION_CHANNEL))

    def test_notification_from_old_worker_after_migration_stays_test(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('legacy-after-migration',{})['id']
            with box.db() as db:
                db.execute("INSERT INTO moderation_notifications(post_id,state) VALUES(?,'sent')",(post,))
            self.assertFalse(box.bind_target(post,PRODUCTION_CHANNEL))
            self.assertEqual(box.queue()[0]['target_channel'],TEST_CHANNEL)

    def test_atomic_reservation_and_bound_approval_resist_legacy_worker(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('race',{})['id']
            key=box.reserve_moderation(post,PRODUCTION_CHANNEL)
            with box.db() as db:
                self.assertEqual(db.execute("INSERT OR IGNORE INTO moderation_notifications(post_id,state) VALUES(?,'sending')",(post,)).rowcount,0)
                db.execute("UPDATE moderation_notifications SET state='sent',message_id=123 WHERE post_id=?",(post,))
            self.assertFalse(box.decide(post,True,1,2,1,[2],channel=PRODUCTION_CHANNEL))
            self.assertFalse(box.decide(post,True,1,2,1,[2],channel=PRODUCTION_CHANNEL,callback_key=key,message_id=999))
            self.assertTrue(box.decide(post,True,1,2,1,[2],channel=PRODUCTION_CHANNEL,callback_key=key,message_id=123))
            self.assertIsNone(box.claim(post,channel=TEST_CHANNEL))
            with box.db() as db:
                self.assertEqual(db.execute("UPDATE posts SET state='sending' WHERE id=? AND state='approved'",(post,)).rowcount,0)
            self.assertIsNotNone(box.claim(post,channel=PRODUCTION_CHANNEL))

    def test_legacy_decision_without_production_approval_proof_cannot_be_sent_or_retried(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('old-decision',{})['id']
            box.reserve_moderation(post,PRODUCTION_CHANNEL)
            with box.db() as db:db.execute("UPDATE posts SET state='approved',moderator=2 WHERE id=?",(post,))
            self.assertIsNone(box.claim(post,channel=PRODUCTION_CHANNEL))
            with box.db() as db:db.execute("UPDATE posts SET state='failed' WHERE id=?",(post,))
            self.assertFalse(box.retry_failed(post))

    def test_callback_is_bound_to_visible_notification_and_wrong_mode_does_not_claim(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'posts.db');post=box.propose('callback',{'text':'fake'})['id']
            key=box.reserve_moderation(post,PRODUCTION_CHANNEL)
            with box.db() as db:db.execute("UPDATE moderation_notifications SET state='sent',message_id=123 WHERE post_id=?",(post,))
            calls=[]
            def fake(request):calls.append(request);return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
            bot=Bot(Settings(bot_token='fake-only',admin_chat='1',moderators=('2',),mode='production',production_enabled=True,channel=PRODUCTION_CHANNEL),httpx.MockTransport(fake))
            try:
                query={'message':{'chat':{'id':1},'message_id':123},'from':{'id':2},'data':f'approve:{post}'}
                self.assertFalse(bot.process_update(box,{'callback_query':query}))
                query['data']=f'approve:{post}:{key}'
                self.assertTrue(bot.process_update(box,{'callback_query':query}))
                self.assertEqual(box.queue()[0]['state'],'approved_bound')
                self.assertEqual(calls,[])
            finally:bot.close()
