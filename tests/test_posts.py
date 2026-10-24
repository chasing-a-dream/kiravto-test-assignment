import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from kiravto.domain import Store
from kiravto.outbox import Outbox
from kiravto.posts import queue_daily,queue_drops,caption

class PostTests(unittest.TestCase):
    def test_daily_and_drop_survive_restart_without_duplicates(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
            path=Path(folder)/'posts.db'
            car={'id':'car','name':'Nissan Maxima','price':100,'posted_at':'2026-10-22T01:00:00+00:00','source_complete':True}
            db.ingest([car],now='2026-10-22T10:00:00+00:00')
            car=dict(car,price=90)
            db.ingest([car],now='2026-10-22T11:00:00+00:00')
            box=Outbox(path)
            queue_daily(db,box,now='2026-10-22T19:00:00+00:00')
            queue_drops(db,box,now='2026-10-22T19:00:00+00:00')
            queue_daily(db,Outbox(path),now='2026-10-22T19:30:00+00:00')
            queue_drops(db,Outbox(path),now='2026-10-22T19:30:00+00:00')
            self.assertEqual(len(box.queue()),2)
    def test_no_null_placeholders_and_discount_terms(self):
        text=caption({'name':None,'year':None,'price':100,'discount_price':90,'discount_conditions':{'credit':10}})
        self.assertNotIn('None',text)
        self.assertIn('кредит',text)
