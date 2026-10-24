import tempfile
import unittest
from pathlib import Path
from kiravto.outbox import Outbox

class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='data')
        self.box = Outbox(Path(self.tmp.name)/'test.db')
    def tearDown(self):
        self.tmp.cleanup()
    def test_duplicate_click_and_restart(self):
        p = self.box.propose('drop:car:2026-10-22', {'text':'Цена снижена'})
        self.assertEqual(p['id'],self.box.propose('drop:car:2026-10-22',{})['id'])
        self.assertTrue(self.box.decide(p['id'],True,1,2,1,[2]))
        self.assertFalse(self.box.decide(p['id'],True,1,2,1,[2]))
        self.assertIsNotNone(self.box.claim(p['id']))
        self.assertIsNone(self.box.claim(p['id']))
        self.box.recover()
        self.assertEqual(self.box.queue()[0]['state'],'uncertain')
    def test_rejected_never_claimed(self):
        p = self.box.propose('new',{})
        with self.assertRaises(PermissionError):
            self.box.decide(p['id'],True,99,2,1,[2])
        self.box.decide(p['id'],False,1,2,1,[2])
        self.assertIsNone(self.box.claim(p['id']))

    def test_explicit_failed_retry_preserves_approval_and_refuses_uncertainty(self):
        p=self.box.propose('repair',{'text':'Same approved content'})['id']
        self.assertFalse(self.box.retry_failed(p))
        self.box.decide(p,True,1,2,1,[2])
        original=self.box.claim(p)
        self.box.finish(p,'failed',error='Telegram rejected request: 400')
        self.assertTrue(self.box.retry_failed(p))
        repaired=self.box.claim(p)
        self.assertEqual(repaired['payload'],original['payload'])
        self.assertEqual(repaired['moderator'],original['moderator'])
        self.box.finish(p,'uncertain')
        self.assertFalse(self.box.retry_failed(p))
