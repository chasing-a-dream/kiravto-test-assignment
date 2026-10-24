import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from tools.configure_agent import configure,hook

class AgentSetupTests(unittest.TestCase):
    def test_fresh_setup_uses_same_docker_database_in_both_configs(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            root=Path(folder).resolve();configure(root)
            toml=tomllib.loads((root/'.codex/config.toml').read_text(encoding='utf-8'))['mcp_servers']['kiravto']
            portable=json.loads((root/'.mcp.json').read_text(encoding='utf-8'))['mcpServers']['kiravto']
            self.assertEqual(toml,portable)
            self.assertEqual(toml['command'],'docker')
            self.assertEqual(toml['args'],['compose','exec','-T','api','python','-m','kiravto','mcp'])
            self.assertEqual(toml['cwd'],root.as_posix())

    @unittest.skipUnless(os.name=='nt','Windows generated hook launcher')
    def test_generated_windows_launcher_runs_with_unicode_workspace(self):
        command=hook('hooks/protect.py')['command']
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],input=json.dumps({'tool_input':{'command':'Write-Output safe-test'}}),encoding='utf-8',capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(result.stdout.strip())
