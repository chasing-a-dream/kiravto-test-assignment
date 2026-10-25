import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from kiravto.api import create_app
from kiravto.config import Settings

class ApiTests(unittest.TestCase):
    def test_auth_health_and_missing(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            client=TestClient(create_app(Settings(db=str(Path(folder)/'api.db'),api_token='test-only')))
            self.assertEqual(client.get('/health').json()['status'],'degraded')
            self.assertEqual(client.get('/offers').status_code,401)
            headers={'Authorization':'Bearer test-only'}
            self.assertEqual(client.get('/offers',headers=headers).json()['total'],0)
            self.assertEqual(client.get('/offers/missing',headers=headers).status_code,404)
            self.assertEqual(client.get('/openapi.json',headers=headers).status_code,200)

    def test_report_reads_new_runs_and_disables_cache(self):
        from kiravto.domain import Store
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'api.db')
            client=TestClient(create_app(Settings(db=path,api_token='test-only')))
            headers={'Authorization':'Bearer test-only'}
            self.assertEqual(client.get('/report').status_code,401)
            self.assertNotIn('#1 ·',client.get('/report',headers=headers).text)
            store=Store(path)
            try: store.ingest([{'id':'1'}],complete=True,now='2026-10-24T22:10:00Z')
            finally: store.close()
            response=client.get('/report',headers=headers)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn('#1 · 25.10.2026 01:10:00 МСК',response.text)
            self.assertIn('Обработано за прогон',response.text)
            self.assertIn('время наблюдения; завершение не сохранено',response.text)

    def test_report_keeps_old_full_run_after_many_incomplete(self):
        from kiravto.domain import Store
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=str(Path(folder)/'api.db')
            store=Store(path)
            try:
                store.ingest([{'id':'1'}],complete=True,now='2026-10-24T20:00:00Z')
                for _ in range(101):
                    store.ingest([],complete=False,now='2026-10-24T21:00:00Z')
            finally: store.close()
            client=TestClient(create_app(Settings(db=path,api_token='test-only')))
            text=client.get('/report',headers={'Authorization':'Bearer test-only'}).text
            self.assertIn('Последний полный прогон: #1 · 24.10.2026 23:00:00 МСК',text)
