"""Live source smoke test, deliberately does not load .env or use Telegram."""
import json
from pathlib import Path
from kiravto.autoru_collector import collect

Path('data').mkdir(exist_ok=True)
print(json.dumps(collect('data/stock.sqlite',max_pages=1,max_details=2),ensure_ascii=False,indent=2))
