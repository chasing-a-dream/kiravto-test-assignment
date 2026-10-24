import unittest
import json
import os
import subprocess
from pathlib import Path
from hooks.protect import reason

class HookTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows hook launch regression')
    def test_configured_commands_launch_under_windows_powershell(self):
        config=json.loads(Path('.codex/hooks.json').read_text(encoding='utf-8'))['hooks']
        root=str(Path.cwd())
        cases=[('PreToolUse',{'tool_input':{'command':'Write-Output safe-test'}},False),('PreToolUse',{'tool_input':{'command':"Write-Output 'PRODUCTION_ENABLED=true'"}},True),('SessionStart',{'cwd':root},False)]
        for name,event,blocked in cases:
            with self.subTest(event=name,blocked=blocked):
                event['hook_event_name']=name
                command=config[name][0]['hooks'][0]['command']
                result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],input=json.dumps(event,ensure_ascii=False),encoding='utf-8',capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                if name=='SessionStart':
                    output=json.loads(result.stdout)['hookSpecificOutput']
                    self.assertEqual(output['hookEventName'],'SessionStart')
                    self.assertTrue(output['additionalContext'])
                elif blocked:
                    self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
                else:self.assertFalse(result.stdout.strip())

    def test_secret_file_and_production_denied(self):
        self.assertIsNotNone(reason({'tool_input':{'command':'Get-Content .env'}}))
        self.assertIsNotNone(reason({'tool_input':{'command':'PRODUCTION_ENABLED=true'}}))
    def test_example_allowed(self):
        self.assertIsNone(reason({'tool_input':{'command':'Get-Content .env.example'}}))
    def test_token_denied(self):
        fake='123456789:'+'a'*35
        self.assertIsNotNone(reason({'tool_input':{'content':fake}}))
