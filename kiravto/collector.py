"""Conservative first-source collector. JSON-LD runs always remain incomplete."""
import hashlib
import json
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from kiravto.source import documents,parse_listing,parse_card,SourceFormatError
from kiravto.domain import Store

BASE='https://auto.ru/diler/cars/used/rolf_yasenevo_moskva/'

class Fetcher:
    def __init__(self,pause=3):
        self.pause=pause
        self.requests=0
    def get(self,url):
        for attempt in range(3):
            time.sleep(self.pause*(2**attempt))
            self.requests+=1
            try:
                with urlopen(Request(url,headers={'User-Agent':'KiravtoInventoryMonitor/0.1'}),timeout=30) as response:
                    return response.url,documents(response.read().decode('utf-8'))
            except HTTPError as exc:
                if exc.code in (403,429):
                    raise SourceFormatError('Source refused request; run incomplete; no protection bypass') from None
                if exc.code<500: raise SourceFormatError('Source HTTP error '+str(exc.code)) from None
            except (URLError,TimeoutError):
                pass
        raise SourceFormatError('Source unavailable after limited retries')

def collect(db_path, max_pages=30, fetcher=None, max_details=None):
    fetcher=fetcher or Fetcher()
    store=Store(db_path)
    started=time.monotonic()
    observed=datetime.now(timezone.utc).isoformat()
    cache_path=Path(db_path).parent/'source-cache.json'
    cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    collected={}
    total=None
    errors=[]
    listing_complete=False
    details=0
    try:
        seen=set()
        for page in range(1,max_pages+1):
            _,data=fetcher.get(BASE+'?page='+str(page))
            rows,reported=parse_listing(data)
            total=reported if total is None else max(total,reported)
            fresh=[r for r in rows if r['id'] not in seen]
            if not fresh: break
            for row in fresh:
                seen.add(row['id'])
                signature=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
                cached=cache.get(row['id'])
                if cached and cached['signature']==signature:
                    card=cached['card']
                else:
                    if max_details is not None and details>=max_details:
                        continue
                    _,card_data=fetcher.get(row['url'])
                    details+=1
                    card=parse_card(card_data)
                    if card['id'] != row['id']:
                        raise SourceFormatError('Card ID does not match requested listing ID')
                    cache[row['id']]={'signature':signature,'card':card}
                collected[row['id']]=card
            if len(seen)>=total:
                listing_complete=True
                break
    except SourceFormatError as exc:
        errors.append(str(exc))
    except BaseException:
        store.close()
        raise
    try:
        # Missing categories and business fields mean this is NEVER a complete run.
        run=store.ingest(list(collected.values()),complete=False,expected_count=total,confirmed_missing=set(),now=observed)
        result={**run,'requests':fetcher.requests,'duration_seconds':round(time.monotonic()-started,2),'errors':errors,'listing_pages_complete':listing_complete,'limitations':['JSON-LD lacks required fields','new inventory not verified','description-only updates not detectable from listing signature']}
        Path(db_path).parent.mkdir(parents=True,exist_ok=True)
        tmp=cache_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
        tmp.replace(cache_path)
        Path(db_path).with_suffix('.last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        store.close()
