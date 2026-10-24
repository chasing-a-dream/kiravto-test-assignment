"""Human-run hidden input; importing this module never reads real settings."""
import argparse
import getpass
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings


def update_settings(path,key):
    if not isinstance(key,str) or '\n' in key or '\r' in key:raise ValueError('Invalid key input')
    path=Path(path)
    if not path.is_file():raise ValueError('Run initial setup first')
    updates={'LLM_API_KEY':key,'LLM_MODEL':'openrouter/free','LLM_ENABLED':'false','LLM_DAILY_BUDGET_USD':'0.01'}
    seen=set();lines=[]
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        name=line.partition('=')[0]
        if name in updates:
            if name not in seen:lines.append(name+'='+updates[name]);seen.add(name)
        else:lines.append(line)
    lines.extend(name+'='+value for name,value in updates.items() if name not in seen)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='\n',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);stream.write('\n'.join(lines)+'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def main():
    parser=argparse.ArgumentParser(description='Store an OpenRouter key with hidden input; leave automatic LLM calls disabled.')
    parser.add_argument('--settings',type=Path,default=Path(__file__).resolve().parents[1]/'.env')
    args=parser.parse_args()
    if not sys.stdin.isatty():parser.error('Run in an interactive terminal; hidden input is required')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('New OpenRouter API key (hidden; never send in chat): ')
    try:
        if not re.fullmatch(r'sk-or-v1-[A-Za-z0-9_-]{20,}',key):parser.error('Invalid OpenRouter key format')
        update_settings(args.settings,key)
    finally:key=None
    print('Saved locally. Automatic LLM calls remain disabled; one free test can be run separately.')


if __name__=='__main__':main()
