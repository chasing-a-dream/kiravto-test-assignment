import json
import tempfile
import unittest
from pathlib import Path
import httpx
from kiravto.outbox import Outbox
from kiravto.config import Settings
from kiravto.telegram import Bot

class ReconciliationTests(unittest.TestCase):
    def test_unknown_notification_can_be_reoffered_only_by_moderator(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'box.db')
            post=box.propose('one',{'text':'one'})['id']
            with box.db() as db:
                db.execute("INSERT INTO moderation_notifications(post_id,message_id,state) VALUES(?,NULL,'uncertain')",(post,))
            calls=[]
            def fake(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
            bot=Bot(Settings(bot_token='fake-only',admin_chat='1',moderators=('2',)),httpx.MockTransport(fake))
            message={'chat':{'id':1},'from':{'id':99},'text':f'/retry_moderation {post}'}
            bot.process_command(box,message)
            self.assertFalse(calls)
            message['from']['id']=2
            bot.process_command(box,message)
            with box.db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM moderation_notifications').fetchone()[0],0)
            self.assertEqual(box.queue()[0]['state'],'pending')
            bot.close()
