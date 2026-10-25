import tempfile
import json
import unittest
from pathlib import Path
import httpx
from kiravto.llm import FactService,cached_facts,failure_details

class LlmTests(unittest.TestCase):
    def test_failure_details_do_not_store_response_text_or_retry(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            calls=[]
            def fake(request):
                calls.append(1)
                return httpx.Response(403,json={'error':'sensitive-response-marker'})
            service=FactService(Path(folder)/'llm.db',key='fake-only',model='test:free',transport=httpx.MockTransport(fake))
            first=service.extract('Один владелец.')
            second=service.extract('Один владелец.')
            self.assertEqual(first['http_status'],403)
            self.assertEqual(second['http_status'],403)
            self.assertEqual(len(calls),1)
            with service.db() as db:row=dict(db.execute('select * from llm_jobs').fetchone())
            self.assertNotIn('sensitive-response-marker',row['error'])
            self.assertIsNone(row['charged_usd'])
            self.assertEqual(row['reserved_usd'],'0.01')
        self.assertEqual(failure_details('Provider response failed; reservation retained'),{})
        self.assertEqual(failure_details({'failure_type':'untrusted','message':'sensitive-response-marker'}),{})

    def test_recorded_live_facts_have_exact_quotes_and_reuse_cache(self):
        fixture=json.loads((Path(__file__).parent/'fixtures'/'openrouter-facts-recorded.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(dir='data') as folder:
            calls=[]
            def fake(request):
                calls.append(1)
                return httpx.Response(200,json=fixture['response'])
            service=FactService(Path(folder)/'llm.db',key='fake-only',model='fake:free',transport=httpx.MockTransport(fake))
            result=service.extract(fixture['description'])
            self.assertEqual(result['status'],'done')
            self.assertEqual(len(result['facts']),3)
            self.assertEqual(result['rejected'],0)
            self.assertTrue(all(f['quote'] in fixture['description'] for f in result['facts']))
            self.assertEqual(cached_facts(service.path,fixture['description']),result['facts'])
            self.assertTrue(service.extract(fixture['description'])['cached'])
            self.assertEqual(len(calls),1)
    def test_invalid_provider_or_usage_envelope_fails_without_retry(self):
        cases=[[],{'usage':None},{'usage':[]},{'usage':{'cost':'NaN'}},{'usage':{'cost':'Infinity'}},{'usage':{'cost':'invalid'}},
               {'choices':{},'usage':{'cost':0}},{'choices':[{'message':[]}],'usage':{'cost':0}}]
        with tempfile.TemporaryDirectory(dir='data') as folder:
            for index,response in enumerate(cases):
                with self.subTest(response=response):
                    calls=[]
                    def fake(request):
                        calls.append(1)
                        return httpx.Response(200,json=response)
                    service=FactService(Path(folder)/f'{index}.db',key='fake-only',model='fake:free',transport=httpx.MockTransport(fake))
                    self.assertEqual(service.extract('description')['status'],'failed')
                    self.assertTrue(service.extract('description')['cached'])
                    self.assertEqual(len(calls),1)
    def test_recorded_empty_final_fails_but_retains_usage_and_cache(self):
        recorded=json.loads((Path(__file__).parent/'fixtures'/'openrouter-empty-final-recorded.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(dir='data') as folder:
            calls=[]
            def fake(request):
                calls.append(request)
                return httpx.Response(200,json=recorded['response'])
            service=FactService(Path(folder)/'llm.db',key='fake-only',model='fake:free',transport=httpx.MockTransport(fake))
            self.assertEqual(service.extract('Один владелец.')['status'],'failed')
            self.assertTrue(service.extract('Один владелец.')['cached'])
            self.assertEqual(len(calls),1)
            stats=service.stats()
            self.assertEqual((stats['input_tokens'],stats['output_tokens'],stats['unknown_cost_jobs']),(164,500,0))
            self.assertEqual(stats['known_cost_usd'],'0')
            self.assertEqual(cached_facts(service.path,'Один владелец.'),[])

    def test_incomplete_or_invalid_final_is_not_a_successful_empty_result(self):
        cases=[('length','{"facts":[]}'),('stop','not json'),('stop','{}'),('stop','[]'),('stop','{"facts":null}'),('stop','')]
        with tempfile.TemporaryDirectory(dir='data') as folder:
            for index,(finish,content) in enumerate(cases):
                with self.subTest(finish=finish,content=content):
                    response={'choices':[{'finish_reason':finish,'message':{'content':content}}],
                              'usage':{'prompt_tokens':10,'completion_tokens':5,'cost':0.01}}
                    service=FactService(Path(folder)/f'{index}.db',key='fake-only',model='fake:free',daily_budget='0.01',transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response)))
                    self.assertEqual(service.extract('description')['status'],'failed')
                    self.assertEqual(service.stats()['known_cost_usd'],'0.01')
                    self.assertEqual(service.extract('different')['status'],'budget_exhausted')

    def test_valid_explicit_empty_facts_is_success(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            response={'choices':[{'finish_reason':'stop','message':{'content':'{"facts":[]}'}}],'usage':{'cost':0}}
            service=FactService(Path(folder)/'llm.db',key='fake-only',model='fake:free',transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response)))
            result=service.extract('Общие рекламные условия.')
            self.assertEqual(result['status'],'done')
            self.assertEqual(result['facts'],[])

    def test_quote_check_cost_cache_budget(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            calls=[]
            def fake(request):
                calls.append(request)
                return httpx.Response(200,json={'choices':[{'message':{'content':'{"facts":[{"fact":"Один владелец","quote":"Один владелец"},{"fact":"Без ДТП","quote":"Без ДТП"}]}'}}],'usage':{'prompt_tokens':30,'completion_tokens':20,'cost':0.01}})
            service=FactService(Path(folder)/'llm.db',key='fake-only',model='fake:free',daily_budget='0.01',transport=httpx.MockTransport(fake))
            first=service.extract('Один владелец. ТО у дилера.')
            self.assertEqual(len(first['facts']),1)
            self.assertEqual(first['rejected'],1)
            self.assertTrue(service.extract('Один владелец. ТО у дилера.')['cached'])
            self.assertEqual(service.extract('Другой автомобиль')['status'],'budget_exhausted')
            self.assertEqual(len(calls),1)
            self.assertEqual(service.stats()['known_cost_usd'],'0.01')
            self.assertEqual(len(cached_facts(service.path,'Один владелец. ТО у дилера.')),1)
            self.assertEqual(cached_facts(service.path,'Описание изменилось'),[])
            self.assertEqual(len(calls),1)
    def test_disabled_and_failure_are_safe(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'llm.db'
            self.assertEqual(FactService(path).extract('text')['status'],'disabled')
            calls=[]
            def broken(request):
                calls.append(1)
                raise httpx.ReadTimeout('fake',request=request)
            service=FactService(path,key='fake-only',model='fake:free',transport=httpx.MockTransport(broken))
            self.assertEqual(service.extract('description')['status'],'failed')
            self.assertTrue(service.extract('description')['cached'])
            self.assertEqual(len(calls),1)
