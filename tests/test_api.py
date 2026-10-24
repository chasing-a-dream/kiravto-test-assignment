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
