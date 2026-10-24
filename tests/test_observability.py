import tempfile
import unittest
from pathlib import Path
from kiravto.domain import Store
from kiravto.observability import record,with_metrics,connection,send_alerts,recover_alerts

class MetricsTests(unittest.TestCase):
    def test_controlled_collection_records_metrics_without_queueing_alerts(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'metrics.db'
            store=Store(path)
            try:
                for hour in (1,2):
                    run=store.ingest([],complete=False,now=f'2026-10-22T0{hour}:00:00+00:00')
                    record(path,{**run,'requests':1},notify=False)
                self.assertEqual(with_metrics(path,store.runs())[0]['requests'],1)
                with connection(path) as db:self.assertEqual(db.execute('SELECT count(*) FROM admin_alerts').fetchone()[0],0)
            finally:store.close()

    def test_two_incomplete_runs_alert_once_and_store_metrics(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'metrics.db'
            store=Store(path)
            try:
                for hour in (1,2,3):
                    run=store.ingest([],complete=False,now=f'2026-10-22T0{hour}:00:00+00:00')
                    record(path,{**run,'requests':4,'duration_seconds':2.5,'errors':[]})
                self.assertEqual(with_metrics(path,store.runs())[0]['requests'],4)
                with connection(path) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_alerts').fetchone()[0],1)
            finally:store.close()

    def test_unknown_alert_is_not_sent_again_after_restart(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'metrics.db'
            with connection(path) as db:
                db.execute("INSERT INTO admin_alerts(dedupe,text) VALUES('one','Неполные прогоны')")
            class Fake:
                calls=0
                def call(self,method,payload):
                    self.calls+=1
                    self.payload=payload
                    raise TimeoutError('fake network break')
            bot=Fake()
            send_alerts(path,bot,'admin-only')
            recover_alerts(path)
            send_alerts(path,bot,'admin-only')
            self.assertEqual(bot.calls,1)
            self.assertEqual(bot.payload['chat_id'],'admin-only')
            with connection(path) as db:
                self.assertEqual(db.execute('SELECT state FROM admin_alerts').fetchone()[0],'uncertain')
