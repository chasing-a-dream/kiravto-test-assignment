import copy
import json
import tempfile
import unittest
from pathlib import Path
from kiravto.autoru_collector import collect,unrestricted_filters,PublicSource,SafeRedirect,gate_parameters,client_config
from kiravto.source import SourceFormatError
from urllib.request import Request
from http.client import IncompleteRead
from urllib.error import URLError,HTTPError
from types import SimpleNamespace
import io

class RisCollectorTests(unittest.TestCase):
    def test_wire_payload_matches_user_captured_edge_request(self):
        fixture=json.loads(Path('tests/fixtures/ris-edge-listing-request.json').read_text(encoding='utf-8'))
        payload=fixture['payload']
        params=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        params['state_group']='USED'
        class Opener:
            def open(self,request,**kwargs):
                self.payload=json.loads(request.data)
                return io.StringIO(json.dumps({'status':'SUCCESS','offers':[],'pagination':{'page':7,'total_page_count':7,'total_offers_count':245},'original_search_parameters':params}))
        source=PublicSource(pause=0)
        source.search_parameters={key:value for key,value in payload.items() if key not in ('page','section','geo_id')}
        source.gate_parameters={'geo_id':payload['geo_id']}
        source.cookies=[SimpleNamespace(name='_csrf_token',value='fake-only')];source.opener=Opener()
        result=source.request_listing('used',7)
        self.assertEqual(source.opener.payload,payload)
        # A syntactically successful empty response is not a full inventory.
        self.assertEqual(result['offers'],[])
        self.assertEqual(result['pagination']['total_offers_count'],245)

    def test_public_client_config_drives_normal_headers_without_fingerprint(self):
        fixture=json.loads(Path('tests/fixtures/ris-client-config.json').read_text(encoding='utf-8'))
        html=''.join('<script id="'+name+'">'+json.dumps(fixture[name])+'</script>' for name in ('app-config','app-config-client'))
        profile=client_config(html)
        class Opener:
            def open(self,request,**kwargs):
                self.request=request
                return io.StringIO('{"status":"SUCCESS"}')
        source=PublicSource(pause=0);source.client_config=profile
        source.cookies=[SimpleNamespace(name='_csrf_token',value='fake-only')];source.opener=Opener()
        source.api_request('listing',{'page':7,'nullable':None})
        request=source.opener.request
        headers={key.lower():value for key,value in request.header_items()}
        self.assertEqual(headers['x-client-app-version'],fixture['app-config']['version'])
        self.assertEqual(headers['x-page-request-id'],'fixture-only-replaced-request-id')
        self.assertEqual(headers['x-retpath-y'],source.referer)
        self.assertTrue(headers['x-client-date'].isdigit())
        self.assertNotIn('x-yafp',headers)
        self.assertIn('nullable',json.loads(request.data))
        self.assertEqual(request.full_url,'https://auto.ru/-/ajax/desktop-search/listing/')

    def test_public_client_config_rejects_foreign_namespace_and_header_injection(self):
        for config in ({},{'version':'1','ajaxPath':'https://example.com/'},{'version':'bad\r\nvalue','ajaxPath':'/-/ajax/desktop-search/'}):
            with self.subTest(config=config),self.assertRaises(SourceFormatError):
                client_config('<script id="app-config">'+json.dumps(config)+'</script>')
        with self.assertRaises(SourceFormatError):
            client_config('<script id="app-config">not JSON</script>')
        for value in ('bad\r\nvalue','bad\x00value','bad value','bad\x7fvalue','не-ASCII'):
            with self.subTest(request_id=value),self.assertRaises(SourceFormatError):
                client_config('<script id="app-config">'+json.dumps({'version':'1','ajaxPath':'/-/ajax/desktop-search/'})+'</script><script id="app-config-client">'+json.dumps({'pageRequestId':value})+'</script>')

    def test_own_html_bootstrap_loads_public_transport_profile(self):
        fixture=json.loads(Path('tests/fixtures/ris-client-config.json').read_text(encoding='utf-8'))
        html=''.join('<script id="'+name+'">'+json.dumps(fixture[name])+'</script>' for name in ('app-config','app-config-client'))
        html+='<script class="RIS" type="application/json">'+json.dumps({'listing':{}})+'</script>'
        class Opener:
            def open(self,*args,**kwargs):
                response=io.BytesIO(html.encode());response.url='https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/'
                return response
        source=PublicSource(pause=0);source.opener=Opener()
        _,state=source.get('https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/')
        self.assertIn('listing',state)
        self.assertEqual(source.client_config['version'],fixture['app-config']['version'])
        self.assertEqual(source.client_config['pageRequestId'],'fixture-only-replaced-request-id')

    def test_native_gate_geo_is_added_to_both_resources_without_overwriting_payload(self):
        fixture=json.loads(Path('tests/fixtures/ris-gate-geo.json').read_text(encoding='utf-8'))
        gate=gate_parameters({'geo':fixture['bootstrap_geo']})
        self.assertEqual(gate,fixture['expected_added_payload'])
        class Opener:
            def __init__(self):self.payloads=[]
            def open(self,request,**kwargs):
                self.payloads.append(json.loads(request.data))
                return io.StringIO('{"status":"SUCCESS"}')
        source=PublicSource(pause=0)
        source.gate_parameters=gate
        source.cookies=[SimpleNamespace(name='_csrf_token',value='fake-only')]
        source.opener=Opener()
        original={'page':7}
        source.api_request('listing',original)
        source.api_request('getOfferCard',{'sale_id':'123','sale_hash':'abcd'})
        source.api_request('listing',{'geo_id':[2]})
        source.api_request('listing',{'geo_id[]':[3]})
        self.assertEqual(original,{'page':7})
        self.assertEqual(source.opener.payloads[0],{'page':7,'geo_id':[1]})
        self.assertEqual(source.opener.payloads[1]['geo_id'],[1])
        self.assertEqual(source.opener.payloads[2],{'geo_id':[2]})
        self.assertEqual(source.opener.payloads[3],{'geo_id[]':[3]})

    def test_unknown_or_narrow_radius_profile_is_not_guessed(self):
        for state in ({},{'geo':{'gids':[]}},{'geo':{'gids':['1'],'radiusAllowed':False}},{'geo':{'gids':[True],'radiusAllowed':False}},{'geo':{'gids':[1]}},{'geo':{'gids':[1],'radiusAllowed':True,'radius':200}}):
            with self.subTest(state=state),self.assertRaises(SourceFormatError):gate_parameters(state)

    def test_lists_all_pages_before_details_and_rejects_changed_card_category(self):
        raw=json.loads(Path('tests/fixtures/ris-card.json').read_text(encoding='utf-8'))
        params=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        class Fake:
            requests=0
            def __init__(self,drift=False):self.calls=[];self.cards={};self.drift=drift
            def listing(self,page):
                self.requests+=1;self.calls.append(('listing',page))
                item=copy.deepcopy(raw)
                item['id']=str(1130000000+page);item['saleId']=item['id']+'-abcdef01';item['idHash']=item['saleId']
                item['url']='https://auto.ru/cars/used/sale/nissan/maxima/'+item['saleId']+'/'
                self.cards[item['saleId']]=item
                return '',{'listing':{'data':{'offers':[item],'pagination':{'page':page,'total_page_count':2,'total_offers_count':2},'original_search_parameters':{**params,'state_group':'ALL'}}}}
            def card(self,identity):
                self.requests+=1;self.calls.append(('card',identity))
                item=copy.deepcopy(self.cards[identity])
                if self.drift:item['section']='new'
                return item
        for drift in (False,True):
            with self.subTest(drift=drift),tempfile.TemporaryDirectory(dir='data') as folder:
                fake=Fake(drift)
                result=collect(str(Path(folder)/'stock.db'),fetcher=fake,notify=False)
                self.assertEqual(fake.calls[:2],[('listing',1),('listing',2)])
                if drift:
                    self.assertEqual(result['count'],0)
                    self.assertTrue(any('category mismatch' in e for e in result['errors']))
                    self.assertEqual(json.loads((Path(folder)/'ris-cache.json').read_text(encoding='utf-8')),{})
                else:
                    self.assertEqual(result['count'],2)
                    self.assertIn('listing_finished_at',result['phases'])
                    self.assertEqual(len(result['listing_evidence']),2)

    def test_real_inactive_cards_confirm_unpublication(self):
        evidence=json.loads(Path('tests/fixtures/ris-inactive-offers.json').read_text(encoding='utf-8'))
        class Fake(PublicSource):
            def api_request(self,name,parameters):return self.raw
        fake=Fake(pause=0)
        for raw in evidence['offers']:
            fake.raw=raw
            result=fake.confirm_missing(raw['saleId'])
            self.assertTrue(result['confirmed'])
            self.assertTrue(result['identity_matches'])
            self.assertEqual(result['status'],'INACTIVE')

    def test_api_retries_transport_once_but_never_403_or_invalid_json(self):
        class Opener:
            def __init__(self,error): self.calls=0; self.error=error
            def open(self,*args,**kwargs):
                self.calls+=1
                if self.calls==1: raise self.error
                return io.StringIO('{"status":"SUCCESS"}')
        fake=PublicSource(pause=0)
        fake.cookies=[SimpleNamespace(name='_csrf_token',value='fake-only')]
        fake.opener=Opener(URLError('simulated connection failure'))
        self.assertEqual(fake.api_request('listing',{})['status'],'SUCCESS')
        self.assertEqual(fake.opener.calls,2)
        fake.opener=Opener(HTTPError('https://auto.ru/',403,'Forbidden',None,None))
        with self.assertRaises(SourceFormatError): fake.api_request('listing',{})
        self.assertEqual(fake.opener.calls,1)
        fake.opener=Opener(ValueError('invalid JSON'))
        with self.assertRaises(SourceFormatError): fake.api_request('listing',{})
        self.assertEqual(fake.opener.calls,1)

    def test_interrupted_response_is_a_source_error(self):
        class Broken:
            def open(self,*args,**kwargs): raise IncompleteRead(b'partial',10)
        fake=PublicSource(pause=0)
        fake.opener=Broken()
        with self.assertRaises(SourceFormatError): fake.image('https://avatars.avto.ru/get-autoru-vos/a/b/orig')
        with self.assertRaises(SourceFormatError): fake.get('https://auto.ru/cars/')

    def test_missing_confirmation_requires_explicit_removed_and_same_identity(self):
        class Fake(PublicSource):
            result={}
            def api_request(self,name,parameters): return self.result
        fake=Fake(pause=0)
        identity='123-abcd'
        for result in [{'saleId':identity,'status':'ACTIVE'},{'saleId':'other','status':'REMOVED'},{'status':'REMOVED'},{'saleId':identity,'status':'UNKNOWN'},{'saleId':identity,'idHash':'other','status':'REMOVED'},{'saleId':identity,'id':'999','status':'REMOVED'}]:
            fake.result=result
            self.assertFalse(fake.confirm_missing(identity)['confirmed'])
        fake.result={'saleId':identity,'status':'REMOVED'}
        self.assertTrue(fake.confirm_missing(identity)['confirmed'])
        fake.result={'saleId':identity,'idHash':identity,'status':'INACTIVE'}
        self.assertTrue(fake.confirm_missing(identity)['confirmed'])
        fake.result['idHash']='different'
        self.assertFalse(fake.confirm_missing(identity)['confirmed'])

    def test_restricted_category_or_main_section_is_rejected(self):
        params=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        class Fake(PublicSource):
            def api_request(self,name,parameters):
                return {'status':'SUCCESS','offers':[],'pagination':{'page':1,'total_page_count':1,'total_offers_count':0},'original_search_parameters':params}
        fake=Fake(pause=0)
        fake.search_parameters={}
        params['state_group']='USED'
        with self.assertRaises(SourceFormatError): fake.request_listing('all',1)
        params['state_group']='NEW'
        params['mark']='BMW'
        with self.assertRaises(SourceFormatError): fake.request_listing('new',1)
        fake.request_listing=lambda section,page:{'pagination':{'total_offers_count':0},'original_search_parameters':params}
        with self.assertRaises(SourceFormatError): fake.category_counts()

    def test_redirect_rejected_before_following_to_another_host(self):
        handler=SafeRedirect()
        for origin,target in [('https://auto.ru/-/ajax/desktop-search/listing/','https://example.com/leak'),('https://avatars.avto.ru/get-autoru-vos/a/b/orig','http://127.0.0.1/orig'),('https://photo.auto.ru/photo/get-autoru-vos/a/b/orig','https://auto.ru/')]:
            with self.assertRaises(SourceFormatError): handler.redirect_request(Request(origin),None,302,'redirect',{},target)
        redirected=handler.redirect_request(Request('https://auto.ru/cars/'),None,302,'redirect',{},'https://auto.ru/cars/used/')
        self.assertEqual(redirected.full_url,'https://auto.ru/cars/used/')

    def test_full_card_then_cached_without_refetch(self):
        raw=json.loads(Path('tests/fixtures/ris-card.json').read_text(encoding='utf-8'))
        raw['color_hex']='040001'
        raw['documents']['owners_number']=4
        listing=copy.deepcopy(raw)
        del listing['description']
        class Fake:
            requests=0
            def get(self,url):
                self.requests+=1
                if '/diler/' in url:
                    return url,{'listing':{'data':{'offers':[listing],'pagination':{'page':1,'total_page_count':1,'total_offers_count':1},'original_search_parameters':{'damage_group':'NOT_BEATEN','customs_state_group':'CLEARED','has_image':True}}}}
                return url,{'card':raw}
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'stock.db')
            first=collect(path,fetcher=Fake())
            self.assertEqual(first['count'],1)
            self.assertEqual(first['requests'],2)
            self.assertFalse(first['complete'])
            cache_path=Path(folder)/'ris-cache.json'
            cache=json.loads(cache_path.read_text(encoding='utf-8'))
            for entry in cache.values():
                # Generated by the adapter in commit 5870a58, before these fixes.
                self.assertEqual(entry['signature'],'83eaf2dd46f46300d06947ee840aeb166517404b82d18c9f69d596d69aa75a71')
                entry['card']['photos']=entry['card'].pop('original_photo_candidates')
                entry['card']['missing_fields']=[]
                entry['card']['color']='040001'
                entry['card'].pop('owners_minimum',None)
            cache_path.write_text(json.dumps(cache),encoding='utf-8')
            second=collect(path,fetcher=Fake())
            self.assertEqual(second['requests'],1)
            self.assertEqual(second['details_fetched'],0)
            from kiravto.domain import Store
            store=Store(path)
            try:
                offer=store.get(raw['saleId'])
                self.assertTrue(offer['photos'][0].endswith('/1200x900'))
                self.assertIn('verified_original_photos',offer['missing_fields'])
                self.assertEqual(offer['color'],'чёрный')
                self.assertTrue(offer['owners_minimum'])
            finally:store.close()

    def test_confirmed_filter_values_and_pagination_preserve_query(self):
        class Fake:
            requests=0
            def __init__(self,drift=False): self.urls=[]; self.drift=drift
            def get(self,url):
                self.requests+=1
                self.urls.append(url)
                page=2 if '&page=2' in url else 1
                parameters=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
                if self.drift and page==1: parameters['damage_group']='NOT_BEATEN'
                raw=json.loads(Path('tests/fixtures/ris-card.json').read_text(encoding='utf-8'))
                raw['id']=str(1130000000+page)
                raw['saleId']=raw['id']+'-abcdef01'
                raw['idHash']=raw['saleId']
                raw['url']='https://auto.ru/cars/used/sale/nissan/maxima/'+raw['saleId']+'/'
                return url,{'listing':{'data':{'offers':[raw],'pagination':{'page':page,'total_page_count':2,'total_offers_count':2},'original_search_parameters':parameters}}}
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'stock.db')
            fake=Fake()
            result=collect(path,fetcher=fake,max_details=0)
            self.assertTrue(result['filters_verified'])
            self.assertNotIn('unfiltered_inventory_not_verified',result['limitations'])
            self.assertIn('damage_group=ANY&customs_state_group=DOESNT_MATTER&page=2',fake.urls[-1])
            self.assertFalse(result['complete'])
            result=collect(path,fetcher=Fake(drift=True),max_details=0)
            self.assertFalse(result['filters_verified'])

    def test_extra_source_restriction_cannot_pass_filter_gate(self):
        native=json.loads(Path('tests/fixtures/ris-native-new-search.json').read_text(encoding='utf-8'))
        self.assertFalse(unrestricted_filters(native['original_search_parameters']))
        parameters=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        self.assertTrue(unrestricted_filters(parameters))
        parameters['mark']='BMW'
        self.assertFalse(unrestricted_filters(parameters))
        del parameters['mark']
        parameters['search_tag']=['real_photo']
        self.assertFalse(unrestricted_filters(parameters))

    def test_api_all_contains_new_category_and_retries_empty_once(self):
        raw=json.loads(Path('tests/fixtures/ris-new-card.json').read_text(encoding='utf-8'))
        class Fake(PublicSource):
            def __init__(self):
                super().__init__(pause=0)
                self.calls=[]
            def get(self,url):
                self.requests+=1
                return url,{'geo':{'gids':[1],'radiusAllowed':False},'listing':{'data':{'offers':[],'pagination':{'total_page_count':1,'total_offers_count':1},'search_parameters':{'dealer_id':'20866489','category':'cars','section':'used'}}}}
            def request_listing(self,section,page):
                self.requests+=1
                self.calls.append((section,page))
                params=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
                params['state_group']=section.upper()
                empty=len(self.calls)==1 or section=='used'
                return {'offers':[] if empty else [raw],'pagination':{'page':1,'total_page_count':1,'total_offers_count':0 if section=='used' else 1},'original_search_parameters':params}
        with tempfile.TemporaryDirectory(dir='data') as folder:
            fake=Fake()
            result=collect(str(Path(folder)/'stock.db'),fetcher=fake,max_details=0)
            self.assertEqual(fake.calls,[('all',1),('all',1),('all',1),('used',1),('all',1)])
            self.assertTrue(result['inventory_complete'])
            self.assertEqual(result['category_counts'],{'newOffersCount':1,'usedOffersCount':0})
            self.assertFalse(result['details_complete'])
            self.assertFalse(result['complete'])

    def test_inventory_partition_compares_sets_and_filters_not_just_counts(self):
        used=json.loads(Path('tests/fixtures/ris-card.json').read_text(encoding='utf-8'))
        new=json.loads(Path('tests/fixtures/ris-new-card.json').read_text(encoding='utf-8'))
        params=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        params['state_group']='ALL'
        observed={used['saleId']:'used',new['saleId']:'new'}
        class Fake(PublicSource):
            def __init__(self,drift=None): super().__init__(pause=0); self.calls=[]; self.drift=drift
            def request_listing(self,section,page):
                self.calls.append((section,page))
                query={**copy.deepcopy(params),'state_group':section.upper()}
                raw=copy.deepcopy(used if section=='used' or page==2 else new)
                if self.drift=='filters' and section=='used': query['has_image']=False
                if self.drift=='restricted' and section=='used': query['damage_group']='NOT_BEATEN'
                final=section=='all' and len(self.calls)==4
                if self.drift=='final_category' and final: raw['section']='used'
                if self.drift=='used_ids' and section=='used' or self.drift=='final_ids' and final:
                    raw['id']='9999999999'; raw['saleId']='9999999999-abcdef01'; raw['idHash']=raw['saleId']
                    raw['url']=raw['url'].replace(raw['url'].strip('/').split('/')[-1],raw['saleId'])
                if self.drift=='duplicate' and section=='all' and page==2: raw=copy.deepcopy(new)
                return {'offers':[raw],'pagination':{'page':page,'total_page_count':2 if section=='all' else 1,'total_offers_count':2 if section=='all' else 1},'original_search_parameters':query}
        good=Fake()
        proof=good.verify_inventory(observed,params,[new['saleId']],2)
        self.assertTrue(proof['verified'])
        self.assertEqual(proof['counts'],{'newOffersCount':1,'usedOffersCount':1})
        self.assertFalse(proof['atomic_snapshot'])
        self.assertEqual(good.calls,[('all',1),('all',2),('used',1),('all',1)])
        for drift in ('filters','restricted','used_ids','final_ids','final_category','duplicate'):
            with self.subTest(drift=drift):
                with self.assertRaises(SourceFormatError): Fake(drift).verify_inventory(observed,params,[new['saleId']],2)
