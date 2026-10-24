import json
import unittest
from pathlib import Path
from kiravto.source import parse_listing, parse_card, SourceFormatError

class SourceTests(unittest.TestCase):
    def test_real_listing(self):
        data = json.loads(Path('tests/fixtures/listing.json').read_text(encoding='utf-8'))
        rows, total = parse_listing(data)
        self.assertEqual(total, 248)
        self.assertEqual(rows[0]['price'], 5890000)
        self.assertEqual(rows[0]['id'], '1160428919-a4898fb5')

    def test_real_card(self):
        data = json.loads(Path('tests/fixtures/card.json').read_text(encoding='utf-8'))
        card = parse_card(data)
        self.assertEqual(card['year'], 2023)
        self.assertEqual(card['brand'], 'Zeekr')
        self.assertTrue(card['photos'])
        self.assertTrue(card['missing_fields'])

    def test_schema_drift_not_silent(self):
        with self.assertRaises(SourceFormatError):
            parse_listing({'offers': []})

if __name__ == '__main__':
    unittest.main()
