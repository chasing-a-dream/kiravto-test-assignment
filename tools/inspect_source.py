import json
import re
from pathlib import Path

s = Path('data/recon/browser-agent.html').read_text(encoding='utf-8')
objs = [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', s, re.S)]
obj = next(x for x in objs if x.get('@type') == 'AggregateOffer')
print(json.dumps(obj['offers'][0], ensure_ascii=False, indent=2))
print('offers', len(obj['offers']), obj['offerCount'])
Path('data/recon/list.json').write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
