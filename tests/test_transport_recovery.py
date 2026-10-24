import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError,URLError
from PIL import Image
from kiravto.autoru_collector import PublicSource,collect
from kiravto.domain import Store
from kiravto.partitioned_source import price_manifest
from kiravto.source import SourceFormatError,SourceTransportError
from test_price_partitions import NativePrices

def jpeg():
    stream=io.BytesIO();Image.new('RGB',(8,6),'red').save(stream,format='JPEG');return stream.getvalue()

class SampleSource:
    def __init__(self,count=3,fail_phase=None,all_fail=False,format_failure=False):
        self.native=NativePrices(prices=tuple(1000+100*i for i in range(count)))
        self.fail_phase=fail_phase;self.all_fail=all_fail;self.format_failure=format_failure
        self.requests=0;self.cards=[];self.images=[];self.confirmations=[]
        for index,raw in enumerate(self.native.offers):
            raw['state']['image_urls'][0]['sizes']['orig']=f'https://avatars.avto.ru/get-autoru-vos/1/photo{index}/orig'
    def inventory_manifest(self,section='all'):return price_manifest(self.native,section)
    def verify_inventory(self,observed,*args):
        return {'verified':True,'counts':{'newOffersCount':sum(v=='new' for v in observed.values()),'usedOffersCount':sum(v=='used' for v in observed.values())}}
    def card(self,identity):
        self.cards.append(identity);self.requests+=1
        if self.fail_phase=='card' and (self.all_fail or identity==self.native.offers[1]['saleId']):
            raise (SourceFormatError('HTTP403; no retry') if self.format_failure else SourceTransportError('simulated card transport'))
        return copy.deepcopy(next(r for r in self.native.offers if r['saleId']==identity))
    def image(self,url):
        self.images.append(url);self.requests+=1
        if self.fail_phase=='photo' and (self.all_fail or '/photo1/' in url):
            raise (SourceFormatError('Source image HTTP403; no retry') if self.format_failure else SourceTransportError('simulated image transport'))
        return jpeg(),'image/jpeg'
    def confirm_missing(self,identity):
        self.confirmations.append(identity)
        raise AssertionError('Missing confirmation must not run with transport failures')

class TransportRecoveryTests(unittest.TestCase):
    def test_recorded_removed_card_with_empty_price_is_inventory_change(self):
        fixture=json.loads(Path('tests/fixtures/ris-removed-during-scan.json').read_text(encoding='utf-8'))
        class Recorded(PublicSource):
            def api_request(self,*args):return fixture['excerpt']
        source=Recorded(pause=0)
        self.assertEqual(fixture['excerpt']['price_info'],{})
        with self.assertRaisesRegex(SourceFormatError,'unpublished during scan') as caught:
            source.card(fixture['excerpt']['saleId'])
        self.assertNotIsInstance(caught.exception,SourceTransportError)

    def test_image_transport_retries_once_with_pacing_but_http_never_retries(self):
        class Opener:
            def __init__(self,error,always=False):self.error=error;self.always=always;self.calls=0
            def open(self,*args,**kwargs):
                self.calls+=1
                if self.always or self.calls==1:raise self.error
                result=io.BytesIO(jpeg());result.headers={'Content-Type':'image/jpeg'};return result
        url='https://avatars.avto.ru/get-autoru-vos/1/photo/orig'
        for always in (False,True):
            source=PublicSource(pause=0);pacing=[];source.wait_for_host=pacing.append
            source.opener=Opener(URLError('simulated timeout'),always)
            if always:
                with self.assertRaises(SourceTransportError):source.image(url)
            else:self.assertEqual(source.image(url)[0],jpeg())
            self.assertEqual((source.opener.calls,source.requests,len(pacing)),(2,2,2))
        for code in (403,429,500):
            source=PublicSource(pause=0);source.opener=Opener(HTTPError(url,code,'denied',None,None))
            with self.assertRaises(SourceFormatError) as caught:source.image(url)
            self.assertNotIsInstance(caught.exception,SourceTransportError)
            self.assertEqual(source.opener.calls,1)

    def test_single_transport_failure_keeps_healthy_rows_and_blocks_removals(self):
        for phase in ('card','photo'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(dir='data') as folder:
                path=str(Path(folder)/'stock.db')
                self.assertTrue(collect(path,fetcher=SampleSource(4),notify=False)['complete'])
                failed=SampleSource(3,phase)
                for raw in failed.native.offers:raw['price_info']['RUR']+=1
                # Expire photo proof to exercise the failing image on the warm scan.
                (Path(folder)/'image-evidence.json').write_text('{}',encoding='utf-8')
                result=collect(path,fetcher=failed,notify=False)
                self.assertEqual((result['count'],result['seen_ids']),(2,3))
                self.assertFalse(result['complete']);self.assertFalse(result['details_complete'])
                self.assertEqual(result['detail_transport_failures'][0]['phase'],phase)
                self.assertIn(failed.native.offers[2]['saleId'],failed.cards)
                self.assertEqual(failed.confirmations,[])
                store=Store(path)
                try:self.assertEqual(store.stock(),{'active':4,'removed':0,'total':4})
                finally:store.close()

    def test_next_clean_run_resumes_checkpoint_without_false_photo_proof(self):
        for phase in ('card','photo'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(dir='data') as folder:
                path=str(Path(folder)/'stock.db');failed=SampleSource(3,phase)
                result=collect(path,fetcher=failed,notify=False)
                self.assertFalse(result['complete'])
                cache=json.loads((Path(folder)/'ris-cache.json').read_text(encoding='utf-8'))
                identity=failed.native.offers[1]['saleId']
                if phase=='photo':self.assertFalse(cache[identity]['card']['photos_original_verified'])
                good=SampleSource();result=collect(path,fetcher=good,notify=False)
                self.assertTrue(result['complete']);self.assertEqual(result['count'],3)
                self.assertEqual(len(good.cards),1 if phase=='card' else 0)
                self.assertEqual(len(good.images),1)

    def test_format_error_aborts_scan_and_transport_outage_has_global_limit(self):
        for phase in ('card','photo'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory(dir='data') as folder:
                bad=SampleSource(5,phase,all_fail=True)
                result=collect(str(Path(folder)/'outage.db'),fetcher=bad,notify=False)
                self.assertFalse(result['complete'])
                self.assertEqual(len(result['detail_transport_failures']),3)
                self.assertEqual(len(bad.cards),3)
                self.assertTrue(any('limit reached' in e for e in result['errors']))
            with tempfile.TemporaryDirectory(dir='data') as folder:
                bad=SampleSource(3,phase,format_failure=True)
                result=collect(str(Path(folder)/'blocked.db'),fetcher=bad,notify=False)
                self.assertFalse(result['complete'])
                self.assertEqual(len(bad.cards),2)
                self.assertEqual(result['detail_transport_failures'],[])
