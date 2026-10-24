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

def fresh_active_offer(offer,current,max_age=7200):
    if not offer or offer.get('status')!='active' or not offer.get('source_complete'):
        return False
    try:
        seen=datetime.fromisoformat(offer.get('last_seen','').replace('Z','+00:00'))
        return seen.tzinfo is not None and 0<=(current-seen).total_seconds()<=max_age
    except (TypeError,ValueError,AttributeError):
        return False

def queue_drops(store,box,min_pct=5,now=None):
    current=datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    day=current.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    added=[]
    seen=set()
    for event in drops(store,days=1,min_pct=min_pct,now=current.isoformat()):
        offer=store.get(event['offer_id'])
        if not fresh_active_offer(offer,current): continue
        field={'price_changed':'price','discount_price_changed':'discount_price'}.get(event['kind'])
        if field is None or offer.get(field)!=event['details']['after']: continue
        key=f'drop:{vehicle_key(offer)}:{day}'
        if key in seen: continue
        seen.add(key)
        offer['facts']=cached_facts(box.path,offer.get('description'))
        label='Цена' if field=='price' else 'Цена со всеми скидками'
        pct=f"{event['pct']:g}".replace('.',',')
        observed=datetime.fromisoformat(event['observed_at']).astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        text='Цена снижена\n'+caption(offer)+'\n'+label+': '+money(event['details']['before'])+' → '+money(event['details']['after'])+f' (−{pct}%).\nИзменение зафиксировано {observed} МСК.'
        added.append(box.propose(key,{'offer_id':offer['id'],'text':text,'photos':offer.get('photos',[])[:10],'change':{'field':field,**event['details'],'observed_at':event['observed_at']}}))
    return added

def queue_daily(store,box,now=None,local_hour=21):
    current=datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    local=current.astimezone(ZoneInfo('Europe/Moscow'))
    if local.hour<local_hour: return []
    day=local.date().isoformat()
    added=[]
    for offer in store.search(limit=10000):
        if not fresh_active_offer(offer,current) or not offer.get('posted_at'): continue
        posted=datetime.fromisoformat(offer['posted_at']).astimezone(ZoneInfo('Europe/Moscow'))
        if posted.date()!=local.date(): continue
        offer['facts']=cached_facts(box.path,offer.get('description'))
        added.append(box.propose(f'daily:{vehicle_key(offer)}:{day}',{'offer_id':offer['id'],'text':'Новые за день · '+day+'\n'+caption(offer),'photos':offer.get('photos',[])[:10]}))
    return added
