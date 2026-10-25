import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from kiravto.api import create_app
from kiravto.config import Settings
from kiravto.domain import Store
from kiravto.health import snapshot
from kiravto.outbox import Outbox
from kiravto.telegram import Bot


class HealthTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(dir='data')
        self.addCleanup(folder.cleanup)
        self.path=str(Path(folder.name)/'health.db')
        self.now=datetime.now(timezone.utc)

    def ingest(self, minutes=0, complete=True):
        with closing(Store(self.path)) as store:
            store.ingest([{'id':'car'}] if complete else [],complete=complete,
                         now=(self.now-timedelta(minutes=minutes)).isoformat())

    def test_empty_stale_incomplete_and_future(self):
        state=snapshot(self.path,self.now)
        self.assertEqual(state['status'],'degraded')
        self.assertIsNone(state['last_complete_run'])
        self.ingest(minutes=121)
        self.assertTrue(snapshot(self.path,self.now)['stale'])
        self.ingest(minutes=1)
        self.assertEqual(snapshot(self.path,self.now)['status'],'ok')
        self.ingest(complete=False)
        state=snapshot(self.path,self.now)
        self.assertEqual(state['status'],'degraded')
        self.assertFalse(state['stale'])
        self.assertEqual(state['last_complete_run']['id'],2)
        self.ingest(minutes=-10)
        self.assertTrue(snapshot(self.path,self.now)['stale'])

    def test_api_uses_same_snapshot(self):
        self.ingest(minutes=1)
        with TestClient(create_app(Settings(db=self.path,api_token='fake-only'))) as client:
            self.assertEqual(client.get('/health').json(),snapshot(self.path,self.now))

    def test_bot_health_permissions_and_no_publication(self):
        self.ingest(minutes=1)
        box=Outbox(self.path)
        post=box.propose('pending-demo',{'text':'Example'})
        calls=[]
        def fake(request):
            self.assertTrue(request.url.path.endswith('/sendMessage'))
            calls.append(json.loads(request.content))
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        bot=Bot(Settings(db=self.path,bot_token='fake-only',admin_chat='1',moderators=('2',)),httpx.MockTransport(fake))
        self.addCleanup(bot.close)
        for chat,user in ((99,2),(1,99)):
            bot.process_update(box,{'message':{'chat':{'id':chat},'from':{'id':user},'text':'/health'}})
        self.assertFalse(calls)
        bot.process_update(box,{'message':{'chat':{'id':1},'from':{'id':2},'text':'/health@demo_bot'}})
        self.assertEqual(len(calls),1)
        self.assertEqual(str(calls[0]['chat_id']),'1')
        self.assertIn('OK',calls[0]['text'])
        self.assertIn('МСК',calls[0]['text'])
        self.assertIn('Последний полный: #1',calls[0]['text'])
        self.assertEqual(box.queue()[0]['state'],'pending')
        self.ingest(complete=False)
        bot.process_update(box,{'message':{'chat':{'id':1},'from':{'id':2},'text':'/health'}})
        self.assertIn('DEGRADED',calls[-1]['text'])
        self.assertIn('Полный сбор за последние 2 часа: да',calls[-1]['text'])

    def test_status_distinguishes_observation_and_saved_finish(self):
        from kiravto.observability import record
        self.ingest(minutes=1)
        box=Outbox(self.path)
        calls=[]
        def fake(request):
            calls.append(json.loads(request.content)['text'])
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        bot=Bot(Settings(bot_token='fake-only',admin_chat='1',moderators=('2',)),httpx.MockTransport(fake))
        self.addCleanup(bot.close)
        message={'chat':{'id':1},'from':{'id':2},'text':'/status'}
        bot.process_command(box,message)
        self.assertIn('Наблюдение (завершение не сохранено)',calls[-1])
        record(self.path,{'id':1,'finished_at':'2026-10-24T22:01:00Z'},notify=False)
        bot.process_command(box,message)
        self.assertIn('Завершён: 25.10.2026 01:01 МСК',calls[-1])
