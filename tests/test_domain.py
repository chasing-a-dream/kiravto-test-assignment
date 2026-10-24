import sqlite3
import tempfile
import unittest
from pathlib import Path
from kiravto.domain import Store

NOW = '2026-10-22T12:00:00+03:00'
VIN = 'WVWZZZ1JZXW000001'


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'domain.sqlite'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def ingest(self, offers, **kw):
        return self.store.ingest(offers, now=NOW, **kw)

    def test_both_prices_and_content_have_immutable_history(self):
        self.ingest([{'id':'1','price':100,'discount_price':90,'description':'a','photos':['a'],'equipment':['x']}])
        self.ingest([{'id':'1','price':95,'discount_price':85,'description':'b','photos':['b'],'equipment':['y']}])
        h = self.store.history('1')
        self.assertEqual(h['observations'][0]['payload']['price'], 100)
        self.assertEqual(len(h['observations']), 2)
        self.assertEqual(len(self.store.drops()), 2)
        self.assertEqual({e['kind'] for e in h['events']}, {'new','price_changed','discount_price_changed','description_changed','photos_changed','equipment_changed'})
        for table in ('observations','events'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute('DELETE FROM ' + table)
            self.store.db.rollback()

    def test_missing_requires_complete_and_confirmation(self):
        self.ingest([{'id':str(i)} for i in range(10)], complete=True)
        self.ingest([{'id':str(i)} for i in range(1,10)], complete=False, confirmed_missing={'0'})
        self.assertEqual(self.store.get('0')['status'], 'active')
        self.ingest([{'id':str(i)} for i in range(1,10)], complete=True)
        self.assertEqual(self.store.get('0')['status'], 'active')
        self.ingest([{'id':str(i)} for i in range(1,10)], complete=True, confirmed_missing={'0','1'})
        self.assertEqual(self.store.get('0')['status'], 'removed')
        self.assertEqual(self.store.get('1')['status'], 'active')
        self.assertEqual(self.store.stock(), {'active':9,'removed':1,'total':10})

    def test_removal_retains_separate_source_confirmation(self):
        self.ingest([{'id':str(i)} for i in range(10)],complete=True)
        evidence={'id':'0','status':'INACTIVE','confirmed':True,'checked_at':NOW}
        self.ingest([{'id':str(i)} for i in range(1,10)],complete=True,confirmed_missing={'0'},missing_evidence={'0':evidence})
        event=next(row for row in self.store.history('0')['events'] if row['kind']=='removed')
        self.assertEqual(event['details']['source_evidence'],evidence)
        self.assertEqual(self.store.get('0')['status'],'removed')

    def test_low_coverage_and_expected_count_block_removal(self):
        self.ingest([{'id':str(i)} for i in range(10)], complete=True)
        run = self.ingest([{'id':'1'}], complete=True, confirmed_missing={'0'})
        self.assertFalse(run['complete'])
        self.assertEqual(run['anomalies'][0]['type'], 'low_coverage')
        run = self.ingest([{'id':str(i)} for i in range(1,10)], complete=True, expected_count=10, confirmed_missing={'0'})
        self.assertFalse(run['complete'])
        self.assertEqual(self.store.get('0')['status'], 'active')
        self.assertEqual(self.store.runs()[-1]['count'], 10)

    def test_return_and_relist_do_not_infer_sale(self):
        self.ingest([{'id':'a','vin':VIN}], complete=False)
        self.ingest([], complete=True, confirmed_missing={'a'})
        self.assertEqual(self.store.get('a')['status'], 'removed')
        self.ingest([{'id':'a','vin':VIN}])
        self.assertEqual(self.store.history('a')['events'][-1]['kind'], 'returned')
        self.ingest([{'id':'b','vin':VIN}])
        event = self.store.history('b')['events'][0]
        self.assertEqual(event['kind'], 'duplicate')
        self.assertEqual(event['details']['previous_ids'], ['a'])
        self.assertEqual(self.store.get('a')['status'], 'active')
        self.ingest([{'id':'c','vin':'***001'}])
        self.assertEqual(self.store.history('c')['events'][0]['kind'], 'new')

    def test_duplicate_ids_and_missing_ids_record_anomaly(self):
        run = self.ingest([{'id':'1'},{'id':'1'},{}], complete=True)
        self.assertFalse(run['complete'])
        self.assertEqual(run['count'], 1)
        self.assertEqual(len(run['anomalies']), 2)

    def test_validation_and_failed_batch_do_not_change_database(self):
        with self.assertRaises(ValueError):
            self.ingest([{'id':'ok'}, {'id':'bad','price':-1}])
        self.assertEqual(self.store.runs(), [])
        self.assertEqual(self.store.stock()['total'], 0)

    def test_persists_and_queries(self):
        self.ingest([{'source_id':7,'brand':'VW','price':500}])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get(7)['price'], 500)
        self.assertEqual(len(self.store.search(brand='VW')), 1)
        self.assertEqual(self.store.search(brand='BMW'), [])
        self.assertIsNone(self.store.get('missing'))


class TransactionTests(unittest.TestCase):
    def test_database_failure_rolls_back_run_offer_and_observation(self):
        store = Store(':memory:')
        try:
            store.ingest([{'id':'1','price':100}], now=NOW)
            store.db.executescript("CREATE TRIGGER fail_observation BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT, 'injected failure'); END;")
            with self.assertRaises(sqlite3.IntegrityError):
                store.ingest([{'id':'1','price':90}], now=NOW)
            self.assertEqual(store.get('1')['price'], 100)
            self.assertEqual(len(store.runs()), 1)
            self.assertEqual(len(store.history('1')['observations']), 1)
            self.assertEqual(store.drops(), [])
        finally:
            store.close()

class IdentityTests(unittest.TestCase):
    def test_removed_vehicle_relisted_by_content_hash(self):
        store=Store(':memory:')
        try:
            store.ingest([{'id':'old','photo_sha256':'a'*64}],now=NOW)
            store.ingest([],complete=True,confirmed_missing={'old'},now=NOW)
            store.ingest([{'id':'new','photo_sha256':'a'*64}],now=NOW)
            event=store.history('new')['events'][0]
            self.assertEqual(event['kind'],'relisted')
            self.assertEqual(event['details']['matched_by'],'photo_content_sha256')
        finally:store.close()
    def test_probable_sale_requires_recent_full_evidence(self):
        store=Store(':memory:')
        try:
            store.ingest([{'id':'car'}],now='2026-10-01T00:00:00+00:00')
            store.ingest([],complete=True,confirmed_missing={'car'},now='2026-10-02T00:00:00+00:00')
            self.assertEqual(store.classify_probable_sales('2026-10-10T00:00:00+00:00',7),[])
            store.ingest([],complete=True,now='2026-10-10T00:00:00+00:00')
            self.assertEqual(store.classify_probable_sales('2026-10-10T00:00:00+00:00',7),['car'])
            store.ingest([{'id':'car'}],now='2026-10-10T01:00:00+00:00')
            self.assertEqual(store.history('car')['events'][-1]['kind'],'returned')
        finally:store.close()

if __name__ == '__main__':
    unittest.main()
