from kiravto.domain import Store
from kiravto.llm import FactService

def enrich(settings):
    if not settings.llm_auto_enabled: return {'status':'manual_only','requests':0}
    if not settings.llm_key: return {'status':'disabled','requests':0}
    service=FactService(settings.db,key=settings.llm_key,model=settings.llm_model,daily_budget=settings.llm_daily_budget)
    store=Store(settings.db)
    counts={}
    try:
        for offer in store.search(limit=10000):
            if not offer.get('source_complete'): continue
            result=service.extract(offer.get('description'))
            status=result['status']
            counts[status]=counts.get(status,0)+1
            if status in ('budget_exhausted','paid_models_not_enabled'): break
        return {'statuses':counts,**service.stats()}
    finally:store.close()
