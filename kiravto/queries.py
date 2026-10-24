"""Shared API/MCP filters and price-drop windows."""
from datetime import datetime,timedelta,timezone

ALIASES={'mb':'Mercedes-Benz','мерседес':'Mercedes-Benz','mercedes':'Mercedes-Benz','mercedes-benz':'Mercedes-Benz'}

def search(store,brand=None,model=None,condition=None,min_price=None,max_price=None,min_year=None,max_year=None,availability=None,limit=100,offset=0):
    if not 1<=limit<=1000 or offset<0: raise ValueError('Invalid pagination')
    brand=ALIASES.get(brand.casefold(),brand) if brand else None
    result=[]
    for row in store.search(limit=10000):
        matched=True
        for field,value in [('brand',brand),('model',model),('condition',condition),('availability',availability)]:
            if value is None: continue
            variants=[row.get(field)]
            if field in ('brand','model'): variants+=row.get(field+'_aliases',[])+[row.get(field+'_code')]
            if value.casefold() not in {str(x).casefold() for x in variants if x is not None}: matched=False
        if not matched: continue
        if any(bound is not None and (row.get(field) is None or (row[field]<bound if lower else row[field]>bound)) for field,bound,lower in [('price',min_price,True),('price',max_price,False),('year',min_year,True),('year',max_year,False)]): continue
        result.append(row)
    return {'total':len(result),'items':result[offset:offset+limit]}

def drops(store,days=7,min_pct=5,now=None):
    if not 1<=days<=365 or not 0<=min_pct<=100: raise ValueError('Invalid drop window')
    current=datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    since=current-timedelta(days=days)
    result=[]
    for row in store.drops(limit=10000):
        before,after=row['details']['before'],row['details']['after']
        pct=(before-after)/before*100 if before else 0
        observed=datetime.fromisoformat(row['observed_at'])
        if since<=observed<=current and pct>=min_pct: result.append(dict(row,pct=round(pct,2)))
    return result
