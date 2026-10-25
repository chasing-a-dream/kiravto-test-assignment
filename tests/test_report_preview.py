import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools import report_preview


class ReportPreviewTests(unittest.TestCase):
    def test_export_and_failure_preserve_snapshot(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            root=Path(folder)
            report=report_preview.Report()
            response=subprocess.CompletedProcess([],0,'<!doctype html><p>fresh</p>','')
            with patch.object(report_preview,'ROOT',root), patch.object(report_preview.subprocess,'run',return_value=response) as run:
                self.assertTrue(report.refresh())
                self.assertEqual(report.response(),(200,response.stdout.encode()))
                for name in ('final-stock-report.html','stock-report.html'):
                    self.assertEqual((root/'output'/name).read_text(),response.stdout)
                self.assertNotIn('shell',run.call_args.kwargs)
                run.return_value=subprocess.CompletedProcess([],1,'','private diagnostic')
                self.assertFalse(report.refresh())
                status,body=report.response()
                self.assertEqual(status,503)
                self.assertNotIn(b'private diagnostic',body)
                self.assertEqual((root/'output/final-stock-report.html').read_text(),response.stdout)
