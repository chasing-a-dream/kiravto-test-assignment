"""Bot transport with durable moderation and conservative unknown-send handling."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
import httpx
from kiravto.outbox import Outbox
from kiravto.source import SourceFormatError

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

class BotError(Exception):
    def __init__(self,code,retry_after=60):
        self.code,self.retry_after=code,retry_after
        super().__init__('Telegram rejected request: '+str(code))

class Bot:
    def __init__(self,settings,transport=None,image_loader=None):
        self.settings=settings
        self.image_loader=image_loader
        self.media_source=None
        self.client=httpx.Client(transport=transport,timeout=25,proxy=settings.telegram_proxy or None)
    def close(self): self.client.close()
    def call(self,method,payload,files=None):
        try:
            url='https://api.telegram.org/bot'+self.settings.bot_token+'/'+method
            if files:
                data={key:json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value) for key,value in payload.items()}
                response=self.client.post(url,data=data,files=files)
            else:response=self.client.post(url,json=payload)
            data=response.json()
            if not isinstance(data,dict):
                raise ValueError('Invalid API response')
        except (httpx.HTTPError,ValueError):
            # No exception URL or response body: token lives in Telegram request path.
            raise TimeoutError('Telegram result unknown; reconcile manually') from None
        if not data.get('ok'):
            raise BotError(data.get('error_code',response.status_code),data.get('parameters',{}).get('retry_after',60))
        return data.get('result')

    def photo_file(self,url):
        if self.image_loader is not None:content,kind=self.image_loader(url)
        else:
            if self.media_source is None:
                from kiravto.autoru_collector import PublicSource
                self.media_source=PublicSource()
            content,kind=self.media_source.image(url)
        if len(content)>10_000_000:raise SourceFormatError('Photo exceeds Telegram upload limit')
        return ('photo.jpg',content,kind)

    def deliver(self,box,post_id):
        row=box.claim(post_id,channel=self.settings.channel)
        if not row: return False
        if (row.get('target_channel') or '@autostock_demo_test')!=self.settings.channel:
            box.finish(post_id,'failed',error='Channel changed since moderation; fresh human approval required')
            return False
        if row['retry_at']>time.time():
            box.finish(post_id,'approved',retry_at=row['retry_at'])
            return False
        try:
            payload=json.loads(row['payload'])
            photos=payload.get('photos',[])[:10]
            text=payload.get('text','Автомобиль')
            if len(photos)>=2:
                files={'photo'+str(index):self.photo_file(url) for index,url in enumerate(photos)}
                media=[{'type':'photo','media':'attach://photo'+str(index)} for index in range(len(photos))]
                media[0]['caption']=text[:1024]
                result=self.call('sendMediaGroup',{'chat_id':self.settings.channel,'media':media},files=files)
                ids=[r['message_id'] for r in result]
            elif photos:
                result=self.call('sendPhoto',{'chat_id':self.settings.channel,'caption':text[:1024]},files={'photo':self.photo_file(photos[0])})
                ids=[result['message_id']]
            else:
                result=self.call('sendMessage',{'chat_id':self.settings.channel,'text':text[:4096]})
                ids=[result['message_id']]
            box.finish(post_id,'sent',message_ids=ids)
            return True
        except BotError as exc:
            retry=exc.code==429
            uncertain=isinstance(exc.code,int) and 500<=exc.code<600
            box.finish(post_id,'approved' if retry else 'uncertain' if uncertain else 'failed',error=str(exc),retry_at=time.time()+exc.retry_after if retry else 0)
        except SourceFormatError:
            box.finish(post_id,'failed',error='Photo unavailable before Telegram upload; no post sent')
        except (TimeoutError,KeyError,TypeError):
            box.finish(post_id,'uncertain',error='Unknown delivery result; manual reconciliation needed')
        return False

    def process_update(self,box,update):
        query=update.get('callback_query')
        if not query:
            self.process_command(box,update.get('message',{}))
            return
        chat=query.get('message',{}).get('chat',{}).get('id')
        user=query.get('from',{}).get('id')
        if str(chat)!=str(self.settings.admin_chat) or str(user) not in self.settings.moderators: return
        parts=query.get('data','').split(':')
        if len(parts) not in (2,3):return
        action,identity=parts[:2]
        if action not in ('approve','reject') or not identity.isdigit(): return
        return box.decide(int(identity),action=='approve',chat,user,self.settings.admin_chat,self.settings.moderators,channel=self.settings.channel,callback_key=parts[2] if len(parts)==3 else None,message_id=query.get('message',{}).get('message_id'))

    def process_command(self,box,message):
        if str(message.get('chat',{}).get('id'))!=str(self.settings.admin_chat) or str(message.get('from',{}).get('id')) not in self.settings.moderators:
            return
        text=message.get('text','')
        command=text.split()[0].split('@')[0] if text else ''
        if command not in ('/health','/status','/drops','/queue','/retry_moderation','/resolve_sent'): return
        from kiravto.domain import Store
        from kiravto.queries import drops
        from kiravto.posts import money
        db=Store(box.path)
        try:
            if command=='/health':
                from kiravto.health import snapshot
                state=snapshot(box.path)
                def moment(stamp):
                    try:
                        parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                        if parsed.utcoffset() is None: raise ValueError('Missing timezone')
                        return parsed.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S МСК')
                    except (ValueError,TypeError,AttributeError): return 'время неизвестно'
                lines=['Состояние данных: '+('OK — свежие, последний прогон полный' if state['status']=='ok' else 'DEGRADED — данные требуют внимания'),
                       'Полный сбор за последние 2 часа: '+('да' if not state['stale'] else 'нет или время неизвестно')]
                for label,row in (('Последний завершённый',state['latest_run']),('Последний полный',state['last_complete_run'])):
                    if not row:
                        lines.append(label+': пока нет.')
                        continue
                    lines.append(label+f": #{row['id']} · "+('полный' if row['complete'] else 'неполный')+f" · обработано {row['count']}")
                    if row.get('finished_at'): lines.append('Завершён: '+moment(row['finished_at']))
                    lines.append('Наблюдение: '+moment(row['observed_at']))
                if state['latest_run'] and state['latest_run'].get('limitations'):
                    lines.append('У последнего прогона есть ограничения полноты; подробности — в отчёте/API.')
                lines.append('Проверка читает базу; не запускает сбор и не проверяет доступность Авто.ру или LLM.')
                reply='\n'.join(lines)
            elif command=='/status':
                from kiravto.observability import with_metrics
                runs=with_metrics(box.path,db.runs(limit=3))
                lines=['Последние прогоны:']
                for run in runs:
                    try:
                        finished = datetime.fromisoformat((run.get('finished_at') or run['observed_at']).replace('Z', '+00:00'))
                        if finished.utcoffset() is None:
                            raise ValueError('Missing timezone')
                        finished_label = finished.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M МСК')
                    except (ValueError, TypeError, AttributeError):
                        finished_label = 'время неизвестно'
                    label='Завершён' if run.get('finished_at') else 'Наблюдение (завершение не сохранено)'
                    lines.append(f"#{run['id']} · "+('полный' if run['complete'] else 'неполный')+f"\n{label}: {finished_label}\nОбработано за прогон: {run['count']} машин")
                if not runs: lines.append('Прогонов пока нет.')
                reply='\n'.join(lines)
            elif command=='/drops':
                events=drops(db,days=7,min_pct=0)
                lines=['Снижения за 7 дней:']
                for event in events[:10]:
                    offer=db.get(event['offer_id']) or {}
                    lines.append((offer.get('name') or event['offer_id'])+': '+money(event['details']['before'])+' → '+money(event['details']['after'])+f" (−{event['pct']}%)")
                if not events: lines.append('Зафиксированных снижений пока нет.')
                reply='\n'.join(lines)
            elif command=='/queue':
                states={'pending':'ждёт модерации','approved':'одобрен, ждёт отправки','approved_bound':'одобрен, ждёт отправки','sending':'отправляется','uncertain':'результат неизвестен, нужна сверка'}
                rows=box.queue()
                reply='Очередь: '+str(len(rows))+'\n'+'\n'.join('#'+str(row['id'])+' · '+states.get(row['state'],row['state']) for row in rows[:15])
                reply+='\nЕсли результат отправки неизвестен, сначала проверьте канал. Автоматического повтора нет.'
            elif command=='/retry_moderation':
                args=text.split()
                if len(args)!=2 or not args[1].isdigit():
                    reply='Формат: /retry_moderation ID. Используйте только после проверки, что карточки модерации нет в группе.'
                else:
                    with box.db() as sql:
                        row=sql.execute("SELECT id FROM posts WHERE id=? AND state='pending'",(int(args[1]),)).fetchone()
                        table=sql.execute("SELECT 1 FROM sqlite_master WHERE name='moderation_notifications'").fetchone()
                        if row and table:
                            changed=sql.execute("DELETE FROM moderation_notifications WHERE post_id=? AND state IN ('uncertain','sending')",(int(args[1]),)).rowcount
                        else: changed=0
                    reply='Повторное предложение поставлено в очередь. Публикация всё равно требует кнопки.' if changed else 'Нет неопределённого уведомления для этого ожидающего поста.'
            else:
                args=text.split()
                if len(args)!=3 or not all(x.isdigit() and int(x)>0 for x in args[1:]):
                    reply='Формат: /resolve_sent ID_поста ID_сообщения. Только если вы лично нашли публикацию в тестовом канале.'
                else:
                    with box.db() as sql:
                        changed=sql.execute("UPDATE posts SET state='sent',message_ids=?,error='Manually reconciled by moderator' WHERE id=? AND state='uncertain'",(json.dumps([int(args[2])]),int(args[1]))).rowcount
                    reply='Отправка отмечена как подтверждённая вами. Повторной публикации нет.' if changed else 'Пост не находится в состоянии неизвестной отправки.'
            try: self.call('sendMessage',{'chat_id':self.settings.admin_chat,'text':reply[:4096]})
            except (BotError,TimeoutError): pass
        finally: db.close()

def worker(settings):
    if not settings.bot_token or not settings.admin_chat or not settings.moderators:
        raise RuntimeError('Bot, admin group and moderator IDs must be configured by the human first')
    box=Outbox(settings.db)
    box.recover()
    bot=Bot(settings)
    from kiravto.observability import recover_alerts,send_alerts
    recover_alerts(settings.db)
    with box.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS bot_state(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS moderation_notifications(post_id INTEGER PRIMARY KEY,message_id INTEGER,state TEXT NOT NULL)')
    def load_offset():
        with box.db() as db:
            row=db.execute("SELECT value FROM bot_state WHERE key='offset'").fetchone()
            return int(row[0]) if row else 0
    try:
        while True:
            try:
                updates=bot.call('getUpdates',{'offset':load_offset(),'timeout':15,'allowed_updates':['message','callback_query']})
                if not isinstance(updates,list): raise TimeoutError('Invalid polling response')
            except BotError as exc:
                time.sleep(max(1,exc.retry_after))
                continue
            except TimeoutError:
                time.sleep(5)
                continue
            for update in updates:
                changed=bot.process_update(box,update)
                if changed:
                    try:
                        bot.call('answerCallbackQuery',{'callback_query_id':update['callback_query']['id'],'text':'Решение сохранено'})
                    except (BotError,TimeoutError):
                        pass
                with box.db() as db:
                    db.execute("INSERT OR REPLACE INTO bot_state VALUES('offset',?)",(str(update['update_id']+1),))
            for row in box.queue():
                if row['state']=='pending':
                    callback_key=box.reserve_moderation(row['id'],settings.channel)
                    if not callback_key:continue
                    payload=json.loads(row['payload'])
                    try:
                        result=bot.call('sendMessage',{'chat_id':settings.admin_chat,'text':('Предложение #'+str(row['id'])+'\nКанал: '+settings.channel+'\n'+payload.get('text','Автомобиль'))[:4096],'reply_markup':{'inline_keyboard':[[{'text':'Опубликовать','callback_data':'approve:'+str(row['id'])+':'+callback_key},{'text':'Отклонить','callback_data':'reject:'+str(row['id'])+':'+callback_key}]]}})
                        with box.db() as db:
                            db.execute("UPDATE moderation_notifications SET message_id=?,state='sent' WHERE post_id=?",(result['message_id'],row['id']))
                    except BotError:
                        with box.db() as db: db.execute('DELETE FROM moderation_notifications WHERE post_id=?',(row['id'],))
                    except TimeoutError:
                        with box.db() as db: db.execute("UPDATE moderation_notifications SET state='uncertain' WHERE post_id=?",(row['id'],))
                elif row['state'] in ('approved','approved_bound'): bot.deliver(box,row['id'])
            send_alerts(settings.db,bot,settings.admin_chat)
    finally: bot.close()
