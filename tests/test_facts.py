import unittest
from kiravto.facts import validate

class FactTests(unittest.TestCase):
    def test_invented_quote_removed(self):
        good,bad=validate('Один владелец. ТО у дилера.',{'facts':[{'fact':'Один владелец','quote':'Один владелец'},{'fact':'Без ДТП','quote':'Без ДТП'}]})
        self.assertEqual(len(good),1)
        self.assertEqual(bad,1)
    def test_malformed_output(self):
        self.assertEqual(validate('text','not JSON'),([],1))
