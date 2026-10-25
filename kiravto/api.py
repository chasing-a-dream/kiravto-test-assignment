import hmac
from contextlib import contextmanager
from datetime import datetime,timezone
from html import escape
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from kiravto.config import Settings
from kiravto.domain import Store
from kiravto.queries import search as search_inventory,drops as query_drops
from kiravto.observability import with_metrics
from kiravto.llm import cached_facts
from kiravto.analytics import calculate,render_report

def create_app(settings=None):
    settings = settings or Settings.load()
    app = FastAPI(title='Киравто',version='0.1.0',docs_url=None,redoc_url=None,openapi_url=None)

    def auth(authorization: str = Header(default='')):
        if not settings.api_token or not hmac.compare_digest(authorization,'Bearer '+settings.api_token):
            raise HTTPException(401,'Invalid or missing bearer token',headers={'WWW-Authenticate':'Bearer'})

    @contextmanager
    def storage():
        store = Store(settings.db)
        try: yield store
        finally: store.close()

    @app.get('/health')
    def health():
        from kiravto.health import snapshot
        return snapshot(settings.db)

    @app.get('/openapi.json',dependencies=[Depends(auth)],include_in_schema=False)
    def schema(): return app.openapi()

    @app.get('/offers',dependencies=[Depends(auth)])
    def offers(brand: str|None=None,model: str|None=None,condition: str|None=None,min_price: int|None=None,max_price: int|None=None,min_year: int|None=None,max_year: int|None=None,availability: str|None=None,limit: int=Query(100,ge=1,le=1000),offset: int=Query(0,ge=0)):
        with storage() as store:
            return search_inventory(store,brand,model,condition,min_price,max_price,min_year,max_year,availability,limit,offset)

    @app.get('/offers/{identity}',dependencies=[Depends(auth)])
    def offer(identity: str):
        with storage() as store: row=store.get(identity)
        if row is None: raise HTTPException(404,'Offer not found')
        return {**row,'facts':cached_facts(settings.db,row.get('description'))}

    @app.get('/offers/{identity}/history',dependencies=[Depends(auth)])
    def history(identity: str):
        with storage() as store:
            if store.get(identity) is None: raise HTTPException(404,'Offer not found')
            return store.history(identity)

    @app.get('/drops',dependencies=[Depends(auth)])
    def drops(days: int=Query(7,ge=1,le=365),min_pct: float=Query(5,ge=0,le=100)):
        with storage() as store: return query_drops(store,days=days,min_pct=min_pct)

    @app.get('/stock',dependencies=[Depends(auth)])
    def stock():
        with storage() as store: return store.stock()

    @app.get('/runs',dependencies=[Depends(auth)])
    def runs():
        with storage() as store: return with_metrics(settings.db,store.runs())

    @app.get('/report',response_class=HTMLResponse,dependencies=[Depends(auth)])
    def report():
        with storage() as store:
            summary=calculate(store,datetime.now(timezone.utc).isoformat())
            rows=with_metrics(settings.db,store.runs())
            summary['recent_runs']=rows[:20]
            full=store.db.execute('SELECT * FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1').fetchone()
            summary['last_complete_run']=with_metrics(settings.db,[dict(full)])[0] if full else None
            return HTMLResponse(render_report(summary),headers={'Cache-Control':'no-store'})
    return app
