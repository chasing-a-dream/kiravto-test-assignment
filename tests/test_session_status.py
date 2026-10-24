import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from hooks.session_status import status

class SessionStatusTests(unittest.TestCase):
    def test_hook_uses_real_service_summary_instead_of_stale_development_db(self):
        response={'latest_finished_run':[10,'2026-10-23T13:31:20+00:00',1,418],'moderation_queue':{'pending':2}}
        with patch('hooks.session_status.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(response))) as run:
            output=status('unused')
        self.assertIn('Docker service snapshot',output)
        self.assertIn('418',output)
        self.assertIn('pending',output)
        self.assertIn('kiravto-api-1',run.call_args.args[0])

    def test_timeout_labels_local_fallback_and_never_claims_current_health(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'data';path.mkdir();db=sqlite3.connect(path/'stock.sqlite')
            db.execute('CREATE TABLE runs(id INTEGER,observed_at TEXT,complete INTEGER,count INTEGER)')
            db.execute("INSERT INTO runs VALUES(1,'old',0,248)");db.commit();db.close()
            with patch('hooks.session_status.subprocess.run',side_effect=subprocess.TimeoutExpired('docker',3)):
                output=status(folder)
            self.assertIn('Development snapshot only',output)
            self.assertIn('248',output)
            self.assertIn('Do not treat it as current Docker state',output)
