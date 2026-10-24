import copy
import json
import unittest
from pathlib import Path
from kiravto.autoru import application_state,normalize,signature
from kiravto.source import SourceFormatError

def fixture(name): return json.loads(Path('tests/fixtures/'+name).read_text(encoding='utf-8'))

class AutoruTests(unittest.TestCase):
    def test_streamed_chunks_can_arrive_reversed(self):
        first='{"listing":{"data":{"offers":[],'
        second='"pagination":{}}}}'
        html=f'<script type="application/json" class="RIS">{second}</script><script type="application/json" class="RIS">{first}</script>'
        self.assertEqual(application_state(html)['listing']['data']['offers'],[])
        with self.assertRaises(SourceFormatError): application_state('<html>blocked</html>')
    def test_real_card_fields(self):
        row=normalize(fixture('ris-card.json'),detail=True)
        self.assertEqual(row['brand'],'Nissan')
        self.assertEqual(row['year'],1996)
        self.assertEqual(row['mileage'],251466)
        self.assertEqual(row['vin'],'JN1**************')
        self.assertEqual(row['vin_visibility'],'partial')
        self.assertTrue(row['photos'][0].endswith('/1200x900'))
        self.assertTrue(row['original_photo_candidates'][0].endswith('/orig'))
        self.assertFalse(row['photos_original_verified'])
        self.assertIn('verified_original_photos',row['missing_fields'])
        self.assertTrue(row['description'])
    def test_discount_and_change_signature(self):
        raw=fixture('ris-discount.json')
        row=normalize(raw)
        self.assertEqual(row['discount_price'],row['price']-raw['discount_options']['max_discount'])
        self.assertIn('credit',row['discount_conditions'])
        changed=copy.deepcopy(raw)
        changed['state']['mileage']+=1
        self.assertNotEqual(signature(raw),signature(changed))
    def test_schema_drift_fails(self):
        raw=fixture('ris-card.json')
        del raw['vehicle_info']['tech_param']
        with self.assertRaises(SourceFormatError): normalize(raw)

    def test_source_black_color_code_confirmed_in_browser(self):
        raw=fixture('ris-card.json')
        raw['color_hex']='040001'
        self.assertEqual(normalize(raw)['color'],'чёрный')

    def test_real_hidden_new_offer_and_group_url(self):
        row=normalize(fixture('ris-new-card.json'),detail=True)
        self.assertEqual(row['id'],'1133539606-d8e950d6')
        self.assertEqual(row['condition'],'new')
        self.assertEqual(row['year'],2026)
        self.assertEqual(row['mileage'],0)
        self.assertEqual(row['price'],19100000)
        self.assertEqual(row['discount_price'],17900000)
