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
            db.ingest([car],now='2026-10-22T18:00:00+00:00')
            car=dict(car,price=90)
            db.ingest([car],now='2026-10-22T18:30:00+00:00')
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

    def test_price_rebound_and_removed_offer_do_not_create_drop_proposals(self):
        for invalid in ('rebound','removed'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
                car={'id':'car','name':'Example','price':100,'source_complete':True}
                db.ingest([car],now='2026-10-22T18:00:00+00:00')
                db.ingest([dict(car,price=90)],now='2026-10-22T18:30:00+00:00')
                if invalid=='rebound':
                    db.ingest([dict(car,price=110)],now='2026-10-22T18:45:00+00:00')
                else:
                    db.db.execute("UPDATE offers SET status='removed' WHERE id='car'")
                    db.db.commit()
                box=Outbox(Path(folder)/'posts.db')
                self.assertEqual(queue_drops(db,box,now='2026-10-22T19:00:00+00:00'),[])
                self.assertEqual(box.queue(),[])

    def test_stale_observation_does_not_create_drop_proposal(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
            car={'id':'car','name':'Example','price':100,'source_complete':True}
            db.ingest([car],now='2026-10-22T10:00:00+00:00')
            db.ingest([dict(car,price=90)],now='2026-10-22T11:00:00+00:00')
            self.assertEqual(queue_drops(db,Outbox(Path(folder)/'posts.db'),now='2026-10-22T19:00:00+00:00'),[])

    def test_both_price_fields_produce_one_current_proposal_with_explicit_price_type(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
            car={'id':'car','name':'Example','price':100,'discount_price':80,'source_complete':True}
            db.ingest([car],now='2026-10-22T18:00:00+00:00')
            db.ingest([dict(car,price=90,discount_price=72)],now='2026-10-22T18:30:00+00:00')
            box=Outbox(Path(folder)/'posts.db')
            self.assertEqual(len(queue_drops(db,box,now='2026-10-22T19:00:00+00:00')),1)
            import json
            payload=json.loads(box.queue()[0]['payload'])
            change=payload['change']
            self.assertEqual(change['after'],db.get('car')[change['field']])
            self.assertIn('Изменение зафиксировано 22.10.2026 21:30 МСК',payload['text'])
            if change['field']=='discount_price':
                self.assertIn('Цена со всеми скидками: 80 ₽ → 72 ₽',payload['text'])
            else:
                self.assertIn('Цена: 100 ₽ → 90 ₽',payload['text'])

    def test_daily_timegate_and_explicit_early_run_keep_real_day_and_dedupe(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
            car={'id':'today','name':'Example','price':100,'posted_at':'2026-10-24T04:50:00+00:00','source_complete':True}
            db.ingest([car],now='2026-10-24T12:00:00+00:00')
            box=Outbox(Path(folder)/'posts.db')
            self.assertEqual(queue_daily(db,box,now='2026-10-24T12:46:00+00:00'),[])
            self.assertEqual(len(queue_daily(db,box,now='2026-10-24T12:46:00+00:00',local_hour=0)),1)
            db.ingest([car],now='2026-10-24T18:30:00+00:00')
            queue_daily(db,box,now='2026-10-24T19:00:00+00:00')
            self.assertEqual(len(box.queue()),1)

    def test_daily_does_not_propose_today_offer_from_a_stale_observation(self):
        with tempfile.TemporaryDirectory(dir='data') as folder, closing(Store(Path(folder)/'posts.db')) as db:
            db.ingest([{'id':'today','name':'Example','price':100,'posted_at':'2026-10-24T04:50:00+00:00','source_complete':True}],now='2026-10-24T12:00:00+00:00')
            box=Outbox(Path(folder)/'posts.db')
            self.assertEqual(queue_daily(db,box,now='2026-10-24T19:00:00+00:00'),[])
            self.assertEqual(box.queue(),[])
