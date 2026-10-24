"""Public HTTP response probe. Never reads secrets or copies browser cookies."""
import json
import re
import sys
import urllib.request
from pathlib import Path

url = sys.argv[1] if len(sys.argv) > 1 else 'https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/'
with urllib.request.urlopen(url, timeout=30) as response:
    html = response.read().decode('utf-8')
    print(json.dumps({'status': response.status, 'url': response.url, 'bytes': len(html)}, ensure_ascii=False))
for attrs, body in re.findall(r'<script\b([^>]*)>(.*?)</script>', html, re.S):
    if len(body) > 1000:
        print(attrs[:160], 'length=', len(body), 'prefix=', body[:160])
Path('data/recon').mkdir(parents=True, exist_ok=True)
Path('data/recon/page.html').write_text(html, encoding='utf-8')
