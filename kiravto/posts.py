"""Human-readable proposals only; this module has no Telegram transport."""
import hashlib
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from kiravto.queries import drops
from kiravto.llm import cached_facts

def money(value): return f'{value:,}'.replace(',',' ')+' ₽' if isinstance(value,int) else 'цена не указана'
def caption(offer):
    specs=[offer.get('name') or 'Автомобиль']
    if offer.get('year') is not None: specs.append(str(offer['year'])+' г.')
    if offer.get('mileage') is not None: specs.append(f"{offer['mileage']:,}".replace(',',' ')+' км')
    text=' · '.join(specs)+'\nЦена: '+money(offer.get('price'))
    if offer.get('discount_price') is not None:
        text+='\nСо всеми скидками: '+money(offer['discount_price'])
        labels={'tradein':'трейд-ин','credit':'кредит','insurance':'страхование'}
        terms=[label for key,label in labels.items() if offer.get('discount_conditions',{}).get(key)]
        if terms: text+=' (условия: '+', '.join(terms)+')'
    if offer.get('url'): text+='\n'+offer['url']
    for fact in offer.get('facts',[])[:3]:
        text+='\n• '+fact['fact']+' — «'+fact['quote']+'»'
    return text

def vehicle_key(offer):
    from kiravto.domain import full_vin
    vin=full_vin(offer.get('vin'))
    return vin or offer.get('photo_sha256') or offer['id']

def queue_drops(store,box,min_pct=5,now=None):
    current=datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    day=current.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    added=[]
    for event in drops(store,days=1,min_pct=min_pct,now=current.isoformat()):
        offer=store.get(event['offer_id'])
        if not offer or not offer.get('source_complete'): continue
        offer['facts']=cached_facts(box.path,offer.get('description'))
        text='Цена снижена\n'+caption(offer)+'\n'+money(event['details']['before'])+' → '+money(event['details']['after'])+f" (−{event['pct']}%)"
        added.append(box.propose(f'drop:{vehicle_key(offer)}:{day}',{'offer_id':offer['id'],'text':text,'photos':offer.get('photos',[])[:10]}))
    return added

def queue_daily(store,box,now=None,local_hour=21):
    current=datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    local=current.astimezone(ZoneInfo('Europe/Moscow'))
    if local.hour<local_hour: return []
    day=local.date().isoformat()
    added=[]
    for offer in store.search(limit=10000):
        if not offer.get('source_complete') or not offer.get('posted_at'): continue
        posted=datetime.fromisoformat(offer['posted_at']).astimezone(ZoneInfo('Europe/Moscow'))
        if posted.date()!=local.date(): continue
        offer['facts']=cached_facts(box.path,offer.get('description'))
        added.append(box.propose(f'daily:{vehicle_key(offer)}:{day}',{'offer_id':offer['id'],'text':'Новые за день · '+day+'\n'+caption(offer),'photos':offer.get('photos',[])[:10]}))
    return added
