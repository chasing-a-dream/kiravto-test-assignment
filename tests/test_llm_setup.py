import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.configure_llm import update_settings


class LlmSetupTests(unittest.TestCase):
    def test_atomic_update_preserves_other_settings_and_deduplicates_llm_key(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'settings.txt'
            unchanged=['TELEGRAM_BOT_TOKEN=fake-bot','API_TOKEN=fake-api','TELEGRAM_MODE=test','PRODUCTION_ENABLED=false','# comment']
            path.write_text('\n'.join(unchanged+['LLM_API_KEY=old','LLM_API_KEY=duplicate','LLM_ENABLED=true']),encoding='utf-8')
            update_settings(path,'fake-llm-only')
            lines=path.read_text(encoding='utf-8').splitlines()
            self.assertEqual(lines[:5],unchanged)
            self.assertEqual(sum(line.startswith('LLM_API_KEY=') for line in lines),1)
            for line in ('LLM_API_KEY=fake-llm-only','LLM_ENABLED=false','LLM_MODEL=openrouter/free','LLM_DAILY_BUDGET_USD=0.01'):self.assertIn(line,lines)
            self.assertEqual(list(Path(folder).iterdir()),[path])

    def test_failed_replace_retains_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'settings.txt';path.write_text('original',encoding='utf-8')
            with patch('tools.configure_llm.os.replace',side_effect=OSError('simulated')):
                with self.assertRaises(OSError):update_settings(path,'fake-only')
            self.assertEqual(path.read_text(encoding='utf-8'),'original')
            self.assertEqual(list(Path(folder).iterdir()),[path])

    def test_newline_key_is_rejected_before_any_file_update(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            path=Path(folder)/'settings.txt';path.write_text('original',encoding='utf-8')
            with self.assertRaises(ValueError):update_settings(path,'bad\nextra=value')
            self.assertEqual(path.read_text(encoding='utf-8'),'original')
