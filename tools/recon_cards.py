import json
import re
import time
import urllib.request
from pathlib import Path

root = Path('data/recon')
listing = json.loads((root / 'list.json').read_text(encoding='utf-8'))
urls = [listing['offers'][0]['url'], 'https://auto.ru/diler/cars/new/rolf_yasenevo_moskva/']
for i, url in enumerate(urls):
    time.sleep(2)
    with urllib.request.urlopen(url, timeout=30) as response:
        html = response.read().decode('utf-8')
        print(response.status, response.url, len(html))
    objs = [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    (root / f'probe-{i}.json').write_text(json.dumps(objs, ensure_ascii=False, indent=2), encoding='utf-8')
    for obj in objs:
        if obj.get('@type') not in ('Organization','BreadcrumbList'):
            print(json.dumps(obj, ensure_ascii=False, indent=2)[:18000])
