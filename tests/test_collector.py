import json
import tempfile
import unittest
from pathlib import Path
from kiravto.collector import collect
from kiravto.domain import Store
from kiravto.source import SourceFormatError

class CollectorTests(unittest.TestCase):
    def test_partial_source_never_complete_and_cached(self):
        listing=json.loads(Path('tests/fixtures/listing.json').read_text(encoding='utf-8'))
        card=json.loads(Path('tests/fixtures/card.json').read_text(encoding='utf-8'))
        class Fake:
            requests=0
            def get(self,url):
                self.requests+=1
                return url, [listing] if '/diler/' in url else card
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'stock.db')
            first=collect(path,1,Fake(),max_details=1)
            self.assertFalse(first['complete'])
            self.assertEqual(first['count'],1)
            fake=Fake()
            second=collect(path,1,fake,max_details=0)
            self.assertEqual(second['count'],1)
            self.assertEqual(fake.requests,1)
    def test_network_failure_preserves_stock(self):
        class Broken:
            requests=1
            def get(self,url): raise SourceFormatError('Network disconnected')
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'stock.db')
            db=Store(path)
            db.ingest([{'id':'old','price':100}],complete=True,now='2026-10-22T01:00:00+00:00')
            db.close()
            result=collect(path,1,Broken())
            self.assertFalse(result['complete'])
            db=Store(path)
            self.assertEqual(db.get('old')['status'],'active')
            db.close()
