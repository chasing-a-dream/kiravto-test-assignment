import copy
import json
import math
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from kiravto.partitioned_source import price_manifest,PartitionedPublicSource
from kiravto.source import SourceFormatError,SourceTransportError,InventoryDriftError


class NativePrices:
    def __init__(self,prices=(100,200,200,400,500,1000),capacity=2):
        self.capacity=capacity;self.calls=[];self.mutate=None
        template=json.loads(Path('tests/fixtures/ris-card.json').read_text(encoding='utf-8'))
        self.parameters=json.loads(Path('tests/fixtures/ris-unfiltered-search.json').read_text(encoding='utf-8'))
        self.offers=[]
        for index,price in enumerate(prices):
            raw=copy.deepcopy(template);identity=str(1130000000+index)+'-abcdef01'
            raw.update(id=identity.split('-')[0],saleId=identity,idHash=identity,url='https://auto.ru/cars/used/sale/nissan/maxima/'+identity+'/',section='used' if index%2==0 else 'new')
            raw['price_info']={'RUR':price+100};raw['discount_options']={'max_discount':100}
            self.offers.append(raw)

    def __call__(self,section,bounds,page=1):
        self.calls.append((section,dict(bounds)))
        rows=[copy.deepcopy(raw) for raw in self.offers if (section=='all' or raw['section']=='used') and ('color' not in bounds or raw['color_hex'] in bounds['color']) and bounds.get('price_from',0)<=raw['price_info']['RUR']-raw['discount_options']['max_discount']<=bounds.get('price_to',10**15)]
        data={'status':'SUCCESS','offers':rows[(page-1)*self.capacity:page*self.capacity],'pagination':{'page':page,'total_offers_count':len(rows),'total_page_count':math.ceil(len(rows)/self.capacity)},'original_search_parameters':{**self.parameters,'state_group':section.upper(),**bounds}}
        if self.mutate:self.mutate(data,bounds,len(self.calls))
        return data


class PricePartitionTests(unittest.TestCase):
    def test_measured_boundary_contract_uses_inclusive_discounted_rubles(self):
        measured=json.loads(Path('tests/fixtures/ris-price-boundary-contract.json').read_text(encoding='utf-8'))
        counts=measured['observed_counts'];total=measured['parent_total']
        self.assertEqual(counts['lte']+counts['gt'],total)
        self.assertEqual(counts['lt']+counts['gte'],total)
        self.assertEqual(counts['lte']-counts['lt'],counts['eq'])
        for raw in measured['exact_price_response_excerpt']['offers_price_excerpt']:
            self.assertEqual(raw['price_info']['RUR']-raw['discount_options']['max_discount'],measured['boundary'])

    def test_disjoint_union_uses_discounted_rubles_and_open_root(self):
        native=NativePrices();result=price_manifest(native,'all')
        self.assertEqual(result['total'],6)
        self.assertEqual(len(result['identities']),6)
        self.assertEqual(native.calls[0],('all',{}))
        self.assertTrue(any('price_from' in bounds and 'price_to' not in bounds for _,bounds in native.calls))
        self.assertEqual(len(result['observations']),6)
        self.assertFalse(result['atomic_snapshot'])
        self.assertEqual(sum(node['total'] for node in result['evidence'] if node['leaf']),6)

    def test_bounds_must_be_echoed_exactly_as_integer(self):
        for mode in ('missing','bool','float'):
            native=NativePrices()
            def mutate(data,bounds,count):
                if bounds:
                    key=next(iter(bounds))
                    if mode=='missing':data['original_search_parameters'].pop(key)
                    else:data['original_search_parameters'][key]=True if mode=='bool' else float(bounds[key])
            native.mutate=mutate
            with self.subTest(mode=mode),self.assertRaises(SourceFormatError):price_manifest(native,'all')

    def test_out_of_range_row_and_changed_neutral_filter_fail(self):
        for mode in ('price','filter'):
            native=NativePrices()
            def mutate(data,bounds,count):
                if bounds:
                    if mode=='price' and data['offers']:data['offers'][0]['price_info']['RUR']=10**12
                    elif mode=='filter':data['original_search_parameters']['damage_group']='NOT_BEATEN'
            native.mutate=mutate
            with self.subTest(mode=mode),self.assertRaises(SourceFormatError):price_manifest(native,'all')

    def test_duplicate_leaf_and_cross_range_identity_fail(self):
        native=NativePrices(prices=(100,200))
        native.offers[1]=copy.deepcopy(native.offers[0])
        with self.assertRaises(SourceFormatError):price_manifest(native,'all')
        native=NativePrices()
        first=native.offers[0]
        native.offers[-1].update({key:first[key] for key in ('id','saleId','idHash','url')})
        with self.assertRaises(SourceFormatError):price_manifest(native,'all')

    def test_child_counts_must_cover_parent(self):
        native=NativePrices()
        def mutate(data,bounds,count):
            if not bounds:data['pagination']['total_offers_count']+=1
        native.mutate=mutate
        with self.assertRaisesRegex(SourceFormatError,'children'):price_manifest(native,'all')

    def test_real_counter_drift_requires_fresh_parent_and_all_unique_rows(self):
        measured=json.loads(Path('tests/fixtures/ris-inventory-drift-counts.json').read_text(encoding='utf-8'))['responses']
        before=measured[0]['pagination']['total_offers_count']
        after=measured[-1]['pagination']['total_offers_count']
        self.assertEqual(sum(r['pagination']['total_offers_count'] for r in measured[1:3]),after)
        native=NativePrices(prices=tuple(range(1,200))+tuple(range(5000001,5000086)),capacity=37)
        def mutate(data,bounds,count):
            if count==1:data['pagination']['total_offers_count']=before
        native.mutate=mutate
        result=price_manifest(native,'all')
        self.assertEqual(result['total'],after)
        self.assertEqual(len(result['identities']),after)
        self.assertEqual(len(result['observations']),after)
        self.assertEqual(result['evidence'][0]['original_total'],before)
        self.assertTrue(any(n.get('kind')=='partition_count_recheck' for n in result['evidence']))

    def test_recheck_rejects_same_counter_with_unenumerated_replacement(self):
        native=NativePrices();root_reads=[]
        def mutate(data,bounds,count):
            if not bounds:
                root_reads.append(1)
                if len(root_reads)==1:data['pagination']['total_offers_count']-=1
                else:
                    raw=data['offers'][0];identity='9999999999-abcdef01'
                    raw.update(id='9999999999',saleId=identity,idHash=identity,url='https://auto.ru/cars/used/sale/nissan/maxima/'+identity+'/')
        native.mutate=mutate
        with self.assertRaisesRegex(InventoryDriftError,'unenumerated IDs'):price_manifest(native,'all')

    def test_drift_recheck_still_honors_request_budget(self):
        native=NativePrices(prices=(100,200,300),capacity=2)
        native.mutate=lambda data,bounds,count:data['pagination'].update(total_offers_count=2) if count==1 else None
        with self.assertRaisesRegex(SourceFormatError,'resource limit'):
            price_manifest(native,'all',max_requests=3)
        self.assertEqual(len(native.calls),3)

    def test_recheck_cannot_accept_empty_or_duplicate_positive_root(self):
        for mode in ('empty','duplicate'):
            native=NativePrices();roots=[]
            def mutate(data,bounds,count):
                if not bounds:
                    roots.append(1)
                    if len(roots)==1:data['pagination']['total_offers_count']-=1
                    elif mode=='empty':data['offers']=[]
                    else:data['offers']=[copy.deepcopy(data['offers'][0])]*2
            native.mutate=mutate
            with self.subTest(mode=mode),self.assertRaisesRegex(InventoryDriftError,'empty or duplicate'):
                price_manifest(native,'all')

    def test_collector_retries_only_drift_once_and_reuses_validated_cards(self):
        from kiravto.autoru_collector import collect
        from kiravto.domain import Store
        native=NativePrices(prices=(100,200))
        for raw in native.offers:
            for photo in raw['state']['image_urls']:
                photo['sizes']['orig']=photo['sizes']['1200x900']
        class Source:
            def __init__(self):self.pause=0;self.requests=0;self.verifications=0;self.fetched=0
            def inventory_manifest(self,section='all'):return price_manifest(self.fetch_price_listing,section)
            def fetch_price_listing(self,section,bounds,page=1):
                self.requests+=1;return native(section,bounds,page)
            def card(self,identity):
                self.requests+=1;self.fetched+=1
                return next(raw for raw in native.offers if raw['saleId']==identity)
            def verify_inventory(self,*args):
                self.verifications+=1
                if self.verifications==1:raise InventoryDriftError('Inventory moved during cards')
                return PartitionedPublicSource.verify_inventory(self,*args)
        with tempfile.TemporaryDirectory(dir='data') as folder,patch('kiravto.autoru_collector.time.sleep'):
            source=Source();path=str(Path(folder)/'stock.db')
            result=collect(path,fetcher=source,notify=False)
            self.assertTrue(result['complete'])
            self.assertEqual(source.verifications,2)
            self.assertEqual(source.fetched,2)
            self.assertEqual([a['complete'] for a in result['collection_attempts']],[False,True])
            self.assertEqual(sum(a['requests'] for a in result['collection_attempts']),source.requests)
            store=Store(path)
            try:
                self.assertEqual(store.stock()['active'],2)
                self.assertEqual(len(store.runs()),2)
                self.assertEqual(store.db.execute("select count(*) from events where kind='removed'").fetchone()[0],0)
            finally:store.close()

    def test_persistent_drift_is_bounded_and_never_combines_attempts(self):
        from kiravto.autoru_collector import collect
        class Source:
            requests=0;calls=0
            def inventory_manifest(self,section='all'):
                self.requests+=1;self.calls+=1
                raise InventoryDriftError('Still moving')
        with tempfile.TemporaryDirectory(dir='data') as folder,patch('kiravto.autoru_collector.time.sleep'):
            source=Source();result=collect(str(Path(folder)/'stock.db'),fetcher=source,notify=False)
            self.assertEqual(source.calls,2)
            self.assertFalse(result['complete'])
            self.assertEqual(result['count'],0)
            self.assertEqual(result['collection_total_requests'],2)

    def test_retry_discards_old_manifest_and_cannot_remove_unconfirmed_missing(self):
        from kiravto.autoru_collector import collect
        from kiravto.domain import Store
        native=NativePrices(prices=(100,200))
        for raw in native.offers:
            for photo in raw['state']['image_urls']:photo['sizes']['orig']=photo['sizes']['1200x900']
        class Source:
            requests=0;checks=0;pause=0
            def inventory_manifest(self,section='all'):return price_manifest(self.fetch_price_listing,section)
            def fetch_price_listing(self,section,bounds,page=1):
                self.requests+=1;return native(section,bounds,page)
            def card(self,identity):return next(raw for raw in native.offers if raw['saleId']==identity)
            def verify_inventory(self,*args):
                self.checks+=1
                if self.checks==1:
                    native.offers.pop(0)
                    raise InventoryDriftError('An ID disappeared during card collection')
                return PartitionedPublicSource.verify_inventory(self,*args)
        with tempfile.TemporaryDirectory(dir='data') as folder,patch('kiravto.autoru_collector.time.sleep'):
            path=str(Path(folder)/'stock.db');result=collect(path,fetcher=Source(),notify=False)
            self.assertEqual([r['count'] for r in result['collection_attempts']],[2,1])
            self.assertEqual(result['seen_ids'],1)
            self.assertFalse(result['complete'])
            self.assertIn('missing_offer_confirmation_required',result['limitations'])
            store=Store(path)
            try:self.assertEqual(store.stock()['active'],2)
            finally:store.close()

    def test_prior_transport_failure_prevents_extra_drift_attempt(self):
        from kiravto.autoru_collector import collect
        native=NativePrices(prices=(100,200))
        class Source:
            requests=0;manifests=0
            def inventory_manifest(self,section='all'):
                self.manifests+=1;return price_manifest(native,section)
            def card(self,identity):raise SourceTransportError('Transport retry already exhausted')
            def verify_inventory(self,*args):raise InventoryDriftError('Inventory also moved')
        with tempfile.TemporaryDirectory(dir='data') as folder,patch('kiravto.autoru_collector.time.sleep') as sleep:
            source=Source();result=collect(str(Path(folder)/'stock.db'),fetcher=source,notify=False)
            self.assertEqual(source.manifests,1)
            self.assertFalse(result['complete'])
            self.assertGreater(len(result['errors']),1)
            sleep.assert_not_called()

    def test_http_schema_and_filter_errors_are_never_collection_retried(self):
        from kiravto.autoru_collector import collect
        class Source:
            requests=0;calls=0
            def inventory_manifest(self,section='all'):
                self.requests+=1;self.calls+=1
                raise SourceFormatError('HTTP403 or changed schema')
        with tempfile.TemporaryDirectory(dir='data') as folder,patch('kiravto.autoru_collector.time.sleep') as sleep:
            source=Source();result=collect(str(Path(folder)/'stock.db'),fetcher=source,notify=False)
            self.assertEqual(source.calls,1)
            self.assertFalse(result['complete'])
            sleep.assert_not_called()

    def test_equal_price_bucket_uses_bounded_pages_without_dropping_rows(self):
        native=NativePrices(prices=(100,100,100))
        result=price_manifest(native,'all')
        self.assertEqual(result['total'],3)
        self.assertEqual(len(result['identities']),3)
        native=NativePrices(prices=(100,)*9)
        with self.assertRaisesRegex(SourceFormatError,'bounded pagination'):price_manifest(native,'all')

    def test_tie_page_duplicate_and_empty_page_fail(self):
        for mode in ('duplicate','empty'):
            native=NativePrices(prices=(100,100,100))
            def mutate(data,bounds,count):
                if data['pagination']['page']==2:
                    data['offers']=[] if mode=='empty' else [copy.deepcopy(native.offers[0])]
            native.mutate=mutate
            with self.subTest(mode=mode),self.assertRaises(SourceFormatError):price_manifest(native,'all')

    def test_color_splits_cover_ties_and_unknown_color_cannot_disappear(self):
        native=NativePrices(prices=(100,)*5)
        for index,raw in enumerate(native.offers):raw['color_hex']=('040001','FAFBFB','97948F')[index%3]
        result=price_manifest(native,'all')
        self.assertEqual(len(result['identities']),5)
        self.assertTrue(any('split_colors' in node for node in result['evidence']))
        native.offers[-1]['color_hex']='UNKNOWN'
        with self.assertRaisesRegex(SourceFormatError,'cover parent'):price_manifest(native,'all')

    def test_color_echo_and_row_color_are_checked(self):
        for mode in ('echo','row'):
            native=NativePrices(prices=(100,)*3)
            def mutate(data,bounds,count):
                if 'color' in bounds:
                    if mode=='echo':data['original_search_parameters'].pop('color')
                    elif data['offers']:data['offers'][0]['color_hex']='UNKNOWN'
            native.mutate=mutate
            with self.subTest(mode=mode),self.assertRaises(SourceFormatError):price_manifest(native,'all')

    def test_measured_color_contract_is_set_based_and_preserves_count(self):
        measured=json.loads(Path('tests/fixtures/ris-color-boundary-contract.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(group['total'] for group in measured['observed_groups']),measured['parent_total'])
        for group in measured['observed_groups']:
            self.assertEqual(set(group['requested_color']),set(group['echoed_color']))
        native=NativePrices(prices=(100,)*3)
        def mutate(data,bounds,count):
            if 'color' in bounds:data['original_search_parameters']['color']=list(reversed(bounds['color']))
        native.mutate=mutate
        self.assertEqual(price_manifest(native,'all')['total'],3)

    def test_limits_and_source_error_stop_without_fallback(self):
        native=NativePrices()
        with self.assertRaisesRegex(SourceFormatError,'resource limit'):price_manifest(native,'all',max_requests=2)
        self.assertEqual(len(native.calls),2)
        calls=[]
        def forbidden(section,bounds):
            calls.append(1);raise SourceFormatError('HTTP403; no protection bypass')
        with self.assertRaisesRegex(SourceFormatError,'HTTP403'):price_manifest(forbidden,'all')
        self.assertEqual(len(calls),1)

    def test_independent_all_used_sets_and_category_change(self):
        native=NativePrices()
        class Source(PartitionedPublicSource):
            def fetch_price_listing(self,section,bounds,page=1):return native(section,bounds,page)
        source=Source(pause=0);initial=source.inventory_manifest()
        proof=source.verify_inventory(initial['identities'],initial['parameters'],[],6)
        self.assertTrue(proof['verified'])
        self.assertEqual(proof['counts'],{'newOffersCount':3,'usedOffersCount':3})
        self.assertEqual(proof['evidence'][-1]['kind'],'final_root')
        native.offers[0]['section']='new'
        with self.assertRaisesRegex(SourceFormatError,'ALL inventory changed'):
            source.verify_inventory(initial['identities'],initial['parameters'],[],6)

    def test_final_root_duplicate_and_invalid_pagination_fail(self):
        for mode in ('duplicate','pagination','missing_total','string_total'):
            native=NativePrices()
            class Source(PartitionedPublicSource):
                def fetch_price_listing(self,section,bounds,page=1):return native(section,bounds,page)
            source=Source(pause=0);initial=source.inventory_manifest();root_calls=[]
            def mutate(data,bounds,count):
                if not bounds and data['original_search_parameters']['state_group']=='ALL':
                    root_calls.append(1)
                    if len(root_calls)==2:
                        if mode=='duplicate':data['offers']=[copy.deepcopy(data['offers'][0])]*2
                        elif mode=='pagination':data['pagination']['total_page_count']=0
                        elif mode=='missing_total':data['pagination'].pop('total_offers_count')
                        else:data['pagination']['total_offers_count']='6'
            native.mutate=mutate
            with self.subTest(mode=mode):
                with self.assertRaises(SourceFormatError) as caught:
                    source.verify_inventory(initial['identities'],initial['parameters'],[],6)
                self.assertNotIsInstance(caught.exception,InventoryDriftError)

    def test_empty_partition_is_proven_without_invented_rows(self):
        result=price_manifest(NativePrices(prices=()),'all')
        self.assertEqual(result['total'],0)
        self.assertEqual(result['identities'],{})

    def test_collector_partition_evidence_and_incomplete_retry_keep_stock(self):
        from kiravto.autoru_collector import collect
        from kiravto.domain import Store
        native=NativePrices()
        for raw in native.offers:
            for photo in raw['state']['image_urls']:
                sizes=photo['sizes'];sizes['orig']=sizes.get('1200x900') or next(iter(sizes.values()))
        class Source:
            requests=0
            def inventory_manifest(self,section='all'):return price_manifest(native,section)
            def fetch_price_listing(self,section,bounds,page=1):return native(section,bounds,page)
            def verify_inventory(self,*args):return PartitionedPublicSource.verify_inventory(self,*args)
            def card(self,identity):return next(raw for raw in native.offers if raw['saleId']==identity)
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'stock.db')
            result=collect(path,fetcher=Source(),notify=False)
            self.assertTrue(result['complete'])
            self.assertEqual(result['adapter'],'autoru_ris_price_partitions')
            self.assertTrue(result['partition_evidence'])
            self.assertEqual(result['count'],6)
            native.mutate=lambda data,bounds,count:data.update(status='ERROR')
            partial=collect(path,fetcher=Source(),notify=False)
            self.assertFalse(partial['complete'])
            store=Store(path)
            try:self.assertEqual(store.stock()['active'],6)
            finally:store.close()
