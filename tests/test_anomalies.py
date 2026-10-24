import unittest
from kiravto.anomalies import mark_model_price_anomalies

class ModelPriceAnomalies(unittest.TestCase):
    def test_tenfold_boundary_keeps_offer_and_other_warnings(self):
        rows={str(index):{'brand_code':'VW','model_code':'GOLF','price':price,'anomalies':['existing'] if index==0 else []} for index,price in enumerate((100,1000,1000))}
        mark_model_price_anomalies(rows)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows['0']['anomalies'],['existing','price_below_tenth_of_model_median'])
        self.assertEqual(rows['0']['model_price_reference'],{'median':1000,'sample_size':3,'basis':'observed_run_base_prices'})
        self.assertEqual(rows['1']['anomalies'],[])

    def test_brand_model_and_base_price_define_comparison(self):
        rows={'a':{'brand':'A','model':'One','price':101,'discount_price':1},'b':{'brand':'A','model':'One','price':1000},'c':{'brand':'A','model':'One','price':1000},'d':{'brand':'B','model':'One','price':1000000},'e':{'brand':'A','model':'Other','price':1000000}}
        mark_model_price_anomalies(rows)
        self.assertTrue(all(not row.get('anomalies') for row in rows.values()))

    def test_missing_price_and_single_offer_do_not_invent_peers(self):
        rows={'a':{'brand':'A','model':'One','price':100},'b':{'brand':'A','model':'One','price':None},'c':{'price':1}}
        mark_model_price_anomalies(rows)
        self.assertTrue(all('model_price_reference' not in row for row in rows.values()))
