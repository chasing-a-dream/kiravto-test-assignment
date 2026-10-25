import argparse
import json
import os
import time
from kiravto.config import Settings

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['api','collect','worker','mcp','bot','facts'])
    parser.add_argument('offer',nargs='?',help='ID или HTTPS-ссылка на одну сохранённую карточку для facts')
    parser.add_argument('--dry-run',action='store_true',help='Предпросмотр facts без обращения к модели')
    parser.add_argument('--json',action='store_true',help='Вывести результат facts как JSON')
    parser.add_argument('--max-pages',type=int,default=30)
    parser.add_argument('--max-details',type=int)
    parser.add_argument('--inventory-strategy',choices=['pages','price-partitions'],default='pages')
    args=parser.parse_args()
    if args.command=='facts' and not args.offer:parser.error('facts требует ID или ссылку на одну карточку')
    if args.command!='facts' and (args.offer or args.dry_run or args.json):parser.error('offer, --dry-run и --json используются только с facts')
    if args.command=='mcp':
        from kiravto.mcp_server import main as serve
        serve()
        return
    settings=Settings.load()
    if args.command=='facts':
        from kiravto.manual_facts import extract_one,render
        try:result=extract_one(settings,args.offer,key=os.getenv('LLM_API_KEY',''),dry_run=args.dry_run)
        except ValueError as exc:parser.error(str(exc))
        print(json.dumps(result,ensure_ascii=False,indent=2) if args.json else render(result))
        if result['status'] not in ('done','preview','empty'):raise SystemExit(1)
        return
    if args.command=='bot':
        from kiravto.telegram import worker
        worker(settings)
        return
    if args.command=='api':
        import uvicorn
        from kiravto.api import create_app
        uvicorn.run(create_app(settings),host='0.0.0.0',port=8000,access_log=False)
    else:
        from kiravto.autoru_collector import collect
        while True:
            started=time.monotonic()
            source=None
            if args.inventory_strategy=='price-partitions':
                from kiravto.partitioned_source import PartitionedPublicSource
                source=PartitionedPublicSource(max_tie_pages=min(4,args.max_pages))
            result=collect(settings.db,args.max_pages,fetcher=source,max_details=args.max_details)
            from kiravto.enrichment import enrich
            result['llm']=enrich(settings)
            from kiravto.domain import Store
            db=Store(settings.db)
            try:
                from datetime import datetime,timezone
                db.classify_probable_sales(datetime.now(timezone.utc).isoformat(),int(os.getenv('PROBABLY_SOLD_DAYS','7')))
                if settings.posts_enabled:
                    from kiravto.outbox import Outbox
                    from kiravto.posts import queue_daily,queue_drops
                    box=Outbox(settings.db)
                    queue_daily(db,box,local_hour=int(os.getenv('DAILY_POST_HOUR_MSK','21')))
                    queue_drops(db,box,min_pct=float(os.getenv('DROP_MIN_PCT','5')))
            finally:db.close()
            print(json.dumps(result,ensure_ascii=False),flush=True)
            if args.command=='collect': break
            interval=max(1800,int(os.getenv('RUN_INTERVAL_SECONDS','3600')))
            time.sleep(max(60,interval-(time.monotonic()-started)))

if __name__=='__main__': main()
