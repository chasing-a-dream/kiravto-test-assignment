import unittest
from kiravto.domain import Store
from kiravto.analytics import calculate,render_report

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:')
        self.addCleanup(self.store.close)

    def test_empty_does_not_fabricate_history(self):
        result=calculate(self.store,'2026-10-22T12:00:00Z')
        self.assertEqual(result['current_stock'],0)
        for field in ('stock_week_ago','mean_days_to_removal','anomaly_share','incomplete_run_share'):
            self.assertIsNone(result[field])
        self.assertIn('нет данных',render_report(result))

    def test_week_snapshot_removal_and_active_medians(self):
        old=[{'id':str(i),'brand':'VW','model':'Golf','price':100+i*10} for i in range(10)]
        self.store.ingest(old,complete=True,now='2026-10-10T12:00:00Z')
        changed=[dict(car) for car in old[1:]]
        changed[0]['price']=50
        changed[0]['discount_price']=40
        self.store.ingest(changed,complete=True,confirmed_missing={'0'},now='2026-10-18T12:00:00Z')
        result=calculate(self.store,'2026-10-22T12:00:00Z')
        self.assertEqual(result['stock_week_ago'],10)
        self.assertEqual(result['current_stock'],9)
        self.assertEqual(result['mean_days_to_removal'],8)
        self.assertEqual(result['medians_by_model'],[{'brand':'VW','model':'Golf','median_price':150,'count':9}])
        self.assertEqual(len(result['top_weekly_drops']),1)
        self.assertEqual(result['top_weekly_drops'][0]['before'],110)
        self.assertEqual(result['incomplete_run_share'],0)

    def test_incomplete_observation_cannot_prove_weekly_stock(self):
        self.store.ingest([{'id':'1','price':100}],now='2026-10-01T00:00:00Z')
        result=calculate(self.store,'2026-10-22T00:00:00Z')
        self.assertIsNone(result['stock_week_ago'])
        self.assertEqual(result['incomplete_run_share'],1)

    def test_timezone_future_exclusion_and_both_prices(self):
        self.store.ingest([{'id':'1','price':100,'discount_price':80}],now='2026-10-21T00:00:00Z')
        self.store.ingest([{'id':'1','price':90,'discount_price':40}],now='2026-10-22T03:00:00+03:00')
        self.store.ingest([{'id':'1','price':1,'discount_price':1},{'id':'future'}],now='2026-10-30T00:00:00Z')
        result=calculate(self.store,'2026-10-22T01:00:00Z')
        self.assertEqual(result['current_stock'],1)
        self.assertEqual(result['run_count'],2)
        self.assertEqual([row['field'] for row in result['top_weekly_drops']],['discount_price','price'])
        self.assertEqual(result['top_weekly_drops'][0]['pct'],50)

    def test_return_starts_separate_removal_episode(self):
        self.store.ingest([{'id':'1'}],now='2026-10-01T00:00:00Z')
        self.store.ingest([],complete=True,confirmed_missing={'1'},now='2026-10-03T00:00:00Z')
        self.store.ingest([{'id':'1'}],now='2026-10-10T00:00:00Z')
        self.store.ingest([],complete=True,confirmed_missing={'1'},now='2026-10-14T00:00:00Z')
        result=calculate(self.store,'2026-10-22T00:00:00Z')
        self.assertEqual(result['removal_episodes'],2)
        self.assertEqual(result['mean_days_to_removal'],3)

    def test_anomalies_count_offers_not_runs_and_duplicate_is_active(self):
        for day in (20,21):
            self.store.ingest([{'id':'1','vin':'WVWZZZ1JZXW000001','anomalies':['bad price']},{'id':'2','vin':'WVWZZZ1JZXW000001','anomalies':[]}],now=f'2026-10-{day}T00:00:00Z')
        result=calculate(self.store,'2026-10-22T00:00:00Z')
        self.assertEqual(result['current_stock'],2)
        self.assertEqual(result['observed_offer_count'],2)
        self.assertEqual(result['anomaly_share'],0.5)

    def test_render_escapes_source_strings(self):
        result=calculate(self.store,'2026-10-22T12:00:00Z')
        result['medians_by_model']=[{'brand':'<script>x</script>','model':'A&B','median_price':1,'count':1}]
        html=render_report(result)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('A&amp;B',html)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError): calculate(self.store,'2026-10-22T12:00:00')
