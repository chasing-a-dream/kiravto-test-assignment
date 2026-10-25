import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx
from kiravto.config import Settings
from kiravto.domain import Store
from kiravto.enrichment import enrich
from kiravto.manual_facts import extract_one,parse_identity,render

CAR='1160076098-ba703ee3'
URL='https://auto.ru/cars/used/sale/hyundai/sonata/'+CAR+'/'

class ManualFactsTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir='data')
        self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'facts.db'
        self.fixture=json.loads(Path('tests/fixtures/openrouter-facts-recorded.json').read_text(encoding='utf-8'))
        store=Store(self.path)
        store.ingest([{'id':CAR,'name':'Тестовая машина','description':self.fixture['description'],'source_complete':True,'price':500000},
                      {'id':'1160000000-abcdef01','description':'Другая машина.','source_complete':True,'price':600000}],
                     complete=True,expected_count=2,now='2026-10-25T08:00:00+00:00')
        store.close()
        self.settings=Settings(db=str(self.path),llm_model='openrouter/free',llm_daily_budget='0.01')
        self.calls=[]
    def transport(self,status=200):
        def fake(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(status,json=self.fixture['response'] if status==200 else {})
        return httpx.MockTransport(fake)
    def test_id_and_canonical_url_are_strict_and_never_fetch_url(self):
        self.assertEqual(parse_identity(CAR),CAR)
        self.assertEqual(parse_identity(URL+'?utm_source=test'),CAR)
        for value in ('http://auto.ru/cars/used/sale/hyundai/sonata/'+CAR+'/',
                      URL.replace('auto.ru','auto.ru.evil.test'),URL.replace('auto.ru','user@auto.ru'),
                      URL.replace('/cars/used/sale/','/other/'),'file:///tmp/'+CAR,CAR+'; echo unsafe'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_identity(value)
    def test_one_description_one_request_and_repeat_is_cached(self):
        transport=self.transport()
        first=extract_one(self.settings,URL,key='fake-only',transport=transport)
        second=extract_one(self.settings,CAR,key='fake-only',transport=transport)
        self.assertEqual(first['status'],'done')
        self.assertEqual(len(first['facts']),3)
        self.assertTrue(second['cached'])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0]['messages'][-1]['content'],self.fixture['description'])
        self.assertFalse(first['automatic_processing'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('select count(*) from llm_jobs').fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from runs').fetchone()[0],1)
            self.assertIsNone(db.execute("select name from sqlite_master where name='posts'").fetchone())
        self.assertNotIn('fake-only',render(first))
    def test_preview_needs_no_key_and_creates_no_llm_job(self):
        with patch('kiravto.manual_facts.FactService',side_effect=AssertionError('No provider service')):
            result=extract_one(self.settings,CAR,dry_run=True)
        self.assertEqual(result['status'],'preview')
        self.assertEqual(result['description_characters'],len(self.fixture['description']))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(db.execute("select name from sqlite_master where name='llm_jobs'").fetchone())
    def test_not_found_and_missing_database_do_not_call_provider(self):
        with patch('kiravto.manual_facts.FactService',side_effect=AssertionError('No provider')):
            self.assertEqual(extract_one(self.settings,'1160000001-abcdef01',key='fake-only')['status'],'not_found')
            absent=Path(self.folder.name)/'absent.db'
            self.assertEqual(extract_one(Settings(db=str(absent)),CAR,key='fake-only')['status'],'not_found')
            self.assertFalse(absent.exists())
    def test_key_missing_paid_model_and_budget_do_not_send_requests(self):
        self.assertEqual(extract_one(self.settings,CAR)['status'],'disabled')
        for model,budget,expected in [('paid-model','0.01','paid_models_not_enabled'),('openrouter/free','0','budget_exhausted')]:
            settings=Settings(db=str(self.path),llm_model=model,llm_daily_budget=budget)
            result=extract_one(settings,CAR,key='fake-only',transport=self.transport())
            self.assertEqual(result['status'],expected)
        self.assertEqual(self.calls,[])
    def test_corrupt_database_or_card_has_clear_error_and_no_provider(self):
        bad=Path(self.folder.name)/'broken.db';bad.write_bytes(b'not a sqlite database')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('update offers set payload=? where id=?',('invalid JSON',CAR));db.commit()
        with patch('kiravto.manual_facts.FactService',side_effect=AssertionError('No provider')):
            for settings in (self.settings,Settings(db=str(bad))):
                self.assertEqual(extract_one(settings,CAR,key='fake-only')['status'],'not_ready')
    def test_failure_is_not_sent_again(self):
        transport=self.transport(status=503)
        first=extract_one(self.settings,CAR,key='fake-only',transport=transport)
        second=extract_one(self.settings,CAR,key='fake-only',transport=transport)
        self.assertEqual(first['status'],'failed')
        self.assertEqual(first['http_status'],503)
        self.assertIn('HTTP 503',render(first))
        self.assertTrue(second['cached'])
        self.assertEqual(second['http_status'],503)
        self.assertEqual(len(self.calls),1)
    def test_corrupted_cached_quotes_are_revalidated_before_output(self):
        transport=self.transport()
        extract_one(self.settings,CAR,key='fake-only',transport=transport)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('update llm_jobs set facts=?',(json.dumps([{'fact':'Неподтверждённый факт','quote':'Цитаты нет в описании'}]),))
            db.commit()
        result=extract_one(self.settings,CAR,key='fake-only',transport=transport)
        self.assertTrue(result['cached'])
        self.assertEqual(result['facts'],[])
        self.assertEqual(result['rejected'],1)
        self.assertNotIn('Неподтверждённый факт',render(result))
        self.assertEqual(len(self.calls),1)
    def test_key_alone_cannot_trigger_automatic_batch(self):
        with patch('kiravto.enrichment.FactService',side_effect=AssertionError('No batch')):
            result=enrich(Settings(db=str(self.path),llm_key='fake-only'))
        self.assertEqual(result,{'status':'manual_only','requests':0})
    def test_real_cli_preview_works_when_automation_disabled_and_hides_key(self):
        env={**os.environ,'DB_PATH':str(self.path.resolve()),'LLM_API_KEY':'fake-only',
             'LLM_ENABLED':'false','LLM_AUTO_ENABLED':'false','TELEGRAM_MODE':'test',
             'PRODUCTION_ENABLED':'false','LLM_MODEL':'openrouter/free','PYTHONUTF8':'1'}
        result=subprocess.run([sys.executable,'-m','kiravto','facts',CAR,'--dry-run','--json'],
                              env=env,capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'preview')
        self.assertNotIn('fake-only',result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
