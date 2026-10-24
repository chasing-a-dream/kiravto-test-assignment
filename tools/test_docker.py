"""Run offline tests without mounting runtime credentials or the stock volume."""
from pathlib import Path
import subprocess

def main():
    root=Path(__file__).resolve().parents[1]
    command=['docker','run','--rm','--network','none','--tmpfs','/app/data:rw,mode=1777']
    for name in ('kiravto','tests','hooks','tools'):
        command.extend(['--mount',f'type=bind,source={root/name},target=/app/{name},readonly'])
    command.extend(['kiravto-api:latest','python','-m','unittest','discover','-s','tests'])
    return subprocess.run(command,check=False).returncode

if __name__=='__main__':raise SystemExit(main())
