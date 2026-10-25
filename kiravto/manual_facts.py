"""One saved car, one explicitly requested fact extraction; no crawl or publication."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from kiravto.facts import validate
from kiravto.llm import FactService,cached_facts

IDENTITY=r'[0-9]+-[A-Za-z0-9]+'

def parse_identity(value):
    if not isinstance(value,str):raise ValueError('Укажи ID объявления или полную ссылку Авто.ру.')
    value=value.strip()
    if re.fullmatch(IDENTITY,value):return value
    try:
        url=urlsplit(value)
        if url.scheme!='https' or url.hostname!='auto.ru' or url.username or url.password or url.port not in (None,443):
            raise ValueError
        match=re.fullmatch(r'/cars/(?:new|used)/sale/[^/]+/[^/]+/('+IDENTITY+r')/?',url.path)
        if match:return match.group(1)
    except ValueError:pass
    raise ValueError('Нужен ID вида 1160076098-ba703ee3 или HTTPS-ссылка на карточку auto.ru.')

def extract_one(settings,identity,*,key='',dry_run=False,transport=None):
    identity=parse_identity(identity)
    if not Path(settings.db).is_file():
        return {'status':'not_found','offer_id':identity,'message':'База ещё не создана. Сначала нужен сбор объявлений.'}
    db=None
    try:
        db=sqlite3.connect(Path(settings.db).resolve().as_uri()+'?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        row=db.execute('select id,payload,status,last_seen from offers where id=?',(identity,)).fetchone()
        offer={**json.loads(row['payload']),'id':row['id'],'status':row['status'],'last_seen':row['last_seen']} if row else None
    except (sqlite3.Error,ValueError,TypeError):
        return {'status':'not_ready','offer_id':identity,'message':'База недоступна или ещё не подготовлена сборщиком.'}
    finally:
        if db is not None:db.close()
    if not offer:
        return {'status':'not_found','offer_id':identity,'message':'Объявление не найдено в сохранённой базе.'}
    result={'offer_id':identity,'name':offer.get('name') or 'Автомобиль',
            'last_seen':offer.get('last_seen'),'offer_status':offer.get('status'),
            'model':settings.llm_model,'automatic_processing':settings.llm_auto_enabled}
    description=offer.get('description')
    if not offer.get('source_complete'):
        return {**result,'status':'not_ready','message':'Подробная карточка ещё не проверена сборщиком.'}
    if not isinstance(description,str) or not description.strip():
        return {**result,'status':'empty','facts':[],'message':'У карточки нет непустого описания.'}
    if dry_run:
        try:stored=cached_facts(settings.db,description)
        except (ValueError,TypeError):stored=[]
        return {**result,'status':'preview','description_characters':len(description),
                'facts':stored,
                'message':'Предпросмотр. Запрос к модели не отправлялся.'}
    if not key:
        return {**result,'status':'disabled','facts':[],
                'message':'Ключ LLM не настроен. Сохрани его локальным скриптом скрытого ввода.'}
    service=FactService(settings.db,key=key,model=settings.llm_model,daily_budget=settings.llm_daily_budget,transport=transport)
    try:extracted=service.extract(description)
    except (ValueError,TypeError):
        return {**result,'status':'invalid_cache','facts':[],
                'message':'Сохранённый результат повреждён. Новый запрос автоматически не отправлен.'}
    result.update(extracted)
    if extracted['status']=='done':
        facts,rejected=validate(description,{'facts':extracted.get('facts',[])})
        result['facts']=facts
        result['rejected']=extracted.get('rejected',0)+rejected
        result['message']='Подтверждённых фактов: '+str(len(facts))+'.'
    else:
        result['facts']=[]
        messages={'budget_exhausted':'Дневной бюджет исчерпан; запрос не отправлен.',
                  'paid_models_not_enabled':'Платные модели запрещены; запрос не отправлен.',
                  'empty':'Описание пустое; запрос не отправлен.',
                  'disabled':'LLM недоступна: проверь ключ и модель.',
                  'failed':'Обработка не удалась. Для этого описания автоматический повтор запрещён.',
                  'pending':'Это описание уже обрабатывается или прежняя попытка была прервана; повтор не отправлен.'}
        result['message']=messages.get(extracted['status'],'Результат обработки сохранён.')
    digest=hashlib.sha256(description.encode()).hexdigest()
    with service.db() as db:
        row=db.execute('select input_tokens,output_tokens,charged_usd from llm_jobs where hash=?',(digest,)).fetchone()
    if row:
        result['usage']={'input_tokens':row['input_tokens'],'output_tokens':row['output_tokens'],
                         'known_cost_usd':row['charged_usd'],
                         'note':'Учёт сохранённого запроса; при cached=true новых расходов на вызов нет.'}
    return result

def render(result):
    lines=[result.get('name','LLM-факты')+' · '+result['offer_id'],result['message']]
    if result.get('status')=='done':
        lines.append('Источник: '+('сохранённый кэш' if result.get('cached') else 'новый ответ модели'))
    if result.get('status')=='preview':
        lines.append('Символов в описании: '+str(result['description_characters']))
    for fact in result.get('facts',[]):
        lines.extend(['• '+fact['fact'],'  Цитата: «'+fact['quote']+'»'])
    if result.get('usage'):
        usage=result['usage'];cost=usage['known_cost_usd'] if usage['known_cost_usd'] is not None else 'неизвестно'
        lines.append(f"Токены сохранённого запроса: {usage['input_tokens']} вход / {usage['output_tokens']} выход. Стоимость, USD: {cost}.")
    return '\n'.join(lines)
