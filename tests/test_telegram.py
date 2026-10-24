import json
import tempfile
import unittest
from pathlib import Path
import httpx
from kiravto.config import Settings
from kiravto.outbox import Outbox
from kiravto.telegram import Bot

class TelegramTests(unittest.TestCase):
    def test_server_error_is_uncertain_without_automatic_retry(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            post=box.propose('server-error',{'text':'Caption'})['id']
            box.decide(post,True,1,2,1,[2])
            calls=[]
            def fake(request):
                calls.append(request)
                return httpx.Response(500,json={'ok':False,'error_code':500})
            bot=Bot(Settings(bot_token='fake-only'),httpx.MockTransport(fake))
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(box.queue()[0]['state'],'uncertain')
            self.assertFalse(box.retry_failed(post))
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(len(calls),1)
            bot.close()

    def test_permanent_rejection_is_retained_without_repeated_delivery(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            post=box.propose('invalid-media',{'text':'Example','photos':['https://example.com/photo.jpg']})['id']
            box.decide(post,True,1,2,1,[2])
            calls=[]
            def fake(request):
                calls.append(request)
                return httpx.Response(400,json={'ok':False,'error_code':400})
            bot=Bot(Settings(bot_token='fake-only'),httpx.MockTransport(fake),image_loader=lambda url:(b'fake-jpeg','image/jpeg'))
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(box.queue()[0]['state'],'failed')
            box.recover()
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(len(calls),1)
            bot.close()

    def test_album_uploads_files_in_order(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            post=box.propose('album',{'text':'Caption','photos':['first','second','third']})['id']
            box.decide(post,True,1,2,1,[2])
            calls=[];loaded=[]
            def load(url):loaded.append(url);return ('image-'+url).encode(),'image/jpeg'
            def fake(request):
                calls.append(request)
                self.assertIn('multipart/form-data',request.headers['content-type'])
                self.assertIn(b'attach://photo0',request.content)
                self.assertIn(b'image-first',request.content)
                return httpx.Response(200,json={'ok':True,'result':[{'message_id':10},{'message_id':11},{'message_id':12}]})
            bot=Bot(Settings(bot_token='fake-only'),httpx.MockTransport(fake),image_loader=load)
            self.assertTrue(bot.deliver(box,post))
            self.assertEqual(loaded,['first','second','third'])
            self.assertEqual(len(calls),1)
            self.assertFalse(bot.deliver(box,post))
            bot.close()

    def test_photo_failure_prevents_any_telegram_send(self):
        from kiravto.source import SourceFormatError
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            post=box.propose('bad-photo',{'text':'Caption','photos':['first','second']})['id']
            box.decide(post,True,1,2,1,[2])
            calls=[]
            def load(url):
                if url=='second':raise SourceFormatError('Unavailable')
                return b'image','image/jpeg'
            def fake(request):
                calls.append(request)
                raise AssertionError('No Telegram request expected')
            bot=Bot(Settings(bot_token='fake-only'),httpx.MockTransport(fake),image_loader=load)
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(box.queue()[0]['state'],'failed')
            self.assertFalse(calls)
            bot.close()

    def test_commands_ignore_other_chats(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            calls=[]
            def fake(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
            bot=Bot(Settings(bot_token='fake-only',admin_chat='1',moderators=('2',)),httpx.MockTransport(fake))
            bot.process_update(box,{'message':{'chat':{'id':99},'from':{'id':2},'text':'/queue'}})
            self.assertFalse(calls)
            bot.process_update(box,{'message':{'chat':{'id':1},'from':{'id':2},'text':'/queue'}})
            self.assertEqual(len(calls),1)
            self.assertIn('Очередь',calls[0]['text'])
            bot.close()
    def test_fake_api_429_restart_and_approval(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            settings=Settings(bot_token='fake-only',admin_chat='1',moderators=('2',))
            calls=[]
            def fake(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json={'ok':True,'result':{'message_id':100}})
            bot=Bot(settings,httpx.MockTransport(fake))
            post=box.propose('one',{'text':'Проверка'})['id']
            update={'callback_query':{'from':{'id':2},'message':{'chat':{'id':99}},'data':f'approve:{post}'}}
            bot.process_update(box,update)
            self.assertFalse(bot.deliver(box,post))
            update['callback_query']['message']['chat']['id']=1
            bot.process_update(box,update)
            self.assertTrue(bot.deliver(box,post))
            bot.process_update(box,update)
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(len(calls),1)
            bot.close()

    def test_429_retains_and_timeout_does_not_duplicate(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            settings=Settings(bot_token='fake-only',admin_chat='1',moderators=('2',))
            post=box.propose('two',{'text':'Проверка'})['id']
            box.decide(post,True,1,2,1,[2])
            def fake(request): return httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':99}})
            bot=Bot(settings,httpx.MockTransport(fake))
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(box.queue()[0]['state'],'approved')
            self.assertGreater(box.queue()[0]['retry_at'],0)
            bot.close()

    def test_timeout_is_uncertain_and_never_retried(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            box=Outbox(Path(folder)/'bot.db')
            post=box.propose('timeout',{'text':'Проверка'})['id']
            box.decide(post,True,1,2,1,[2])
            calls=[]
            def fake(request):
                calls.append(request)
                raise httpx.ReadTimeout('simulated',request=request)
            bot=Bot(Settings(bot_token='fake-only'),httpx.MockTransport(fake))
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(box.queue()[0]['state'],'uncertain')
            box.recover()
            self.assertFalse(bot.deliver(box,post))
            self.assertEqual(len(calls),1)
            bot.close()
