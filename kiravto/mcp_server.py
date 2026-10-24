import os
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from mcp.server import MCPServer
from kiravto.domain import Store
from kiravto.outbox import Outbox
from kiravto.queries import search,drops
from kiravto.llm import cached_facts

server=MCPServer('Киравто',instructions='Данные наблюдений могут быть неполными. Проверяй статус прогонов. Публикация требует человека; прямого инструмента отправки нет.')

def db_path():
    path=os.getenv('DB_PATH','data/stock.sqlite')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    return path

@contextmanager
def storage():
    db=Store(db_path())
    try: yield db
    finally: db.close()

@server.tool()
def search_cars(brand: str|None=None, model: str|None=None, limit: int=50) -> dict:
    """Искать наблюдаемые машины по марке и модели. Звать для подбора; полноту проверить через run_status."""
    with storage() as db: result=search(db,brand=brand,model=model,limit=limit)
    status=run_status()
    return {**result,'source_contract_complete':status['source_contract_complete'],'warning':'Перед выводами проверь даты и полноту через run_status'}

@server.tool()
def car_details(offer_id: str) -> dict:
    """Получить карточку объявления по ID, включая явные отсутствующие поля. Звать после поиска."""
    with storage() as db: offer=db.get(offer_id)
    return {**offer,'facts':cached_facts(db_path(),offer.get('description'))} if offer else {'error':'Объявление не найдено'}

@server.tool()
def car_history(offer_id: str) -> dict:
    """Получить версии и события объявления с датами, чтобы обосновать изменение цены."""
    with storage() as db: return db.history(offer_id)

@server.tool()
def price_drops(days: int=7, min_pct: float=5) -> dict:
    """Посмотреть зафиксированные снижения обеих цен. История отражает только наблюдения, не гарантирует полный рынок."""
    with storage() as db: return {'items':drops(db,days=days,min_pct=min_pct)}

@server.tool()
def stock_summary() -> dict:
    """Получить число наблюдаемых активных и снятых объявлений. Для оценки достоверности затем вызвать run_status."""
    with storage() as db: return db.stock()

@server.tool()
def run_status(limit: int=5) -> dict:
    """Краткие статусы последних прогонов. Полные доказательства — HTTP /runs; неполный прогон не подтверждает снятие."""
    if type(limit) is not int or not 1<=limit<=20:
        raise ValueError('limit must be 1..20')
    from kiravto.observability import with_metrics
    with storage() as db: runs=with_metrics(db_path(),db.runs(limit=limit))
    for run in runs:
        # Evidence stays durable in SQLite/API; do not consume model context
        # with hundreds of IDs, hashes and per-card timestamps for a status.
        run.pop('listing_evidence',None)
        run.pop('detail_evidence',None)
        partitions=run.pop('partition_evidence',None)
        if partitions is not None:run['partition_node_count']=len(partitions)
        proof=run.get('category_proof')
        if isinstance(proof,dict):
            run['category_proof']={key:value for key,value in proof.items() if key!='evidence'}
            run['category_proof']['evidence_page_count']=len(proof.get('evidence',[]))
    verified=bool(runs and runs[0]['complete'] and not runs[0].get('limitations'))
    return {'runs':runs,'source_contract_complete':verified}

@server.tool()
def propose_post(offer_id: str, reason: str='manual') -> dict:
    """Предложить пост человеку на модерацию. Не публикует; возвращает предупреждение об обязательном одобрении."""
    with storage() as db: offer=db.get(offer_id)
    if not offer: return {'error':'Объявление не найдено'}
    offer['facts']=cached_facts(db_path(),offer.get('description'))
    day=datetime.now(timezone.utc).date().isoformat()
    from kiravto.posts import caption
    post=Outbox(db_path()).propose(f'mcp:{offer_id}:{day}',{'offer_id':offer_id,'reason':reason,'text':caption(offer),'photos':offer.get('photos',[])[:10]})
    return {**post,'warning':'Только предложение. Нужна модерация человека; прямой публикации нет.'}

def main(): server.run()
if __name__=='__main__': main()
