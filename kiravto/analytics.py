"""Observed inventory analytics. Removal does not imply a sale."""
import json
from datetime import datetime,timedelta,timezone
from html import escape
from statistics import mean,median

def _time(value):
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None: raise ValueError('Timezone-aware timestamp required')
    return stamp.astimezone(timezone.utc)

def calculate(store,now):
    end=_time(now)
    week=end-timedelta(days=7)
    runs=[dict(row) for row in store.db.execute('SELECT * FROM runs') if _time(row['observed_at'])<=end]
    events=sorted((dict(row) for row in store.db.execute('SELECT * FROM events') if _time(row['observed_at'])<=end),key=lambda row:(_time(row['observed_at']),row['id']))
    active,active_week,starts=set(),set(),{}
    durations,drops=[],[]
    for event in events:
        identity,kind,stamp=event['offer_id'],event['kind'],_time(event['observed_at'])
        if kind in ('new','returned','relisted','duplicate'):
            active.add(identity)
            starts[identity]=stamp
            if stamp<=week: active_week.add(identity)
        elif kind=='removed':
            active.discard(identity)
            if stamp<=week: active_week.discard(identity)
            started=starts.pop(identity,None)
            if started is not None and stamp>=started: durations.append((stamp-started).total_seconds()/86400)
        elif kind in ('price_changed','discount_price_changed') and week<=stamp:
            details=json.loads(event['details'])
            before,after=details.get('before'),details.get('after')
            if type(before) in (int,float) and type(after) in (int,float) and before>0 and 0<=after<before:
                drops.append({'offer_id':identity,'field':kind.removesuffix('_changed'),'before':before,'after':after,'amount':before-after,'pct':round((before-after)/before*100,2),'observed_at':event['observed_at']})
    latest={}
    for row in sorted(store.db.execute('SELECT * FROM observations'),key=lambda row:(_time(row['observed_at']),row['id'])):
        if _time(row['observed_at'])<=end: latest[row['offer_id']]=json.loads(row['payload'])
    groups={}
    for identity in active:
        payload=latest.get(identity,{})
        model,price=payload.get('model'),payload.get('price')
        if isinstance(model,str) and model.strip() and type(price) in (int,float) and price>0:
            groups.setdefault((str(payload.get('brand') or 'Марка не указана'),model),[]).append(price)
    medians=[{'brand':brand,'model':model,'median_price':median(prices),'count':len(prices)} for (brand,model),prices in sorted(groups.items())]
    historical=any(row['complete'] and _time(row['observed_at'])<=week for row in runs)
    anomalous=sum(isinstance(payload.get('anomalies'),list) and bool(payload['anomalies']) for payload in latest.values())
    return {'as_of':now,'current_stock':len(active),'stock_week_ago':len(active_week) if historical else None,'mean_days_to_removal':round(mean(durations),3) if durations else None,'removal_episodes':len(durations),'medians_by_model':medians,'top_weekly_drops':sorted(drops,key=lambda row:(-row['pct'],-row['amount'],row['offer_id'],row['field']))[:10],'anomaly_share':anomalous/len(latest) if latest else None,'anomalous_offer_count':anomalous,'observed_offer_count':len(latest),'incomplete_run_share':sum(not row['complete'] for row in runs)/len(runs) if runs else None,'run_count':len(runs),'complete_run_count':sum(bool(row['complete']) for row in runs)}

def render_report(summary):
    def value(item,percent=False):
        return 'нет данных' if item is None else escape(f'{item*100:.1f}%' if percent else str(item))
    metrics=[('Наблюдаемый склад сейчас','current_stock',False),('Наблюдаемый склад неделю назад','stock_week_ago',False),('Среднее время до подтверждённого снятия, дней','mean_days_to_removal',False),('Доля аномальных объявлений','anomaly_share',True),('Доля неполных прогонов','incomplete_run_share',True)]
    items=''.join('<tr><th>'+escape(label)+'</th><td>'+value(summary.get(key),percent)+'</td></tr>' for label,key,percent in metrics)
    models=''.join('<tr><td>'+escape(str(row['brand']))+'</td><td>'+escape(str(row['model']))+'</td><td>'+value(row['median_price'])+'</td><td>'+value(row['count'])+'</td></tr>' for row in summary.get('medians_by_model',[]))
    drops=''.join('<tr><td>'+escape(str(row['offer_id']))+'</td><td>'+('Цена со скидкой' if row['field']=='discount_price' else 'Цена')+'</td><td>'+value(row['before'])+'</td><td>'+value(row['after'])+'</td><td>'+value(row['pct'])+'%</td></tr>' for row in summary.get('top_weekly_drops',[]))
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Киравто — отчёт</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#18283b;background:#f4f6f8}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd}h2{margin-top:32px}.scroll{overflow-x:auto}</style></head><body><h1>Наблюдения за складом</h1><p>На '+escape(str(summary.get('as_of','нет данных')))+'</p><p>Склад отражает наблюдаемые объявления, а не гарантированную полноту источника. Снятие не означает продажу. Недельное значение восстанавливается по событиям только при наличии полного прогона до исторической даты; последующие неполные прогоны не доказывают полный склад. Среднее время считается по завершённым эпизодам наблюдения.</p><p>Доля аномалий: объявления с непустым списком anomalies в последней версии / все различные наблюдавшиеся объявления, включая снятые. Доля неполных прогонов: неполные / все прогоны. При нулевом знаменателе — нет данных.</p><table>'+items+'</table><h2>Медианы цен активных объявлений по моделям, ₽</h2>'+('<div class="scroll"><table><tr><th>Марка</th><th>Модель</th><th>Медиана</th><th>Объявлений</th></tr>'+models+'</table></div>' if models else '<p>нет данных</p>')+'<h2>Наибольшие снижения за последние 7 дней</h2>'+('<div class="scroll"><table><tr><th>ID</th><th>Тип цены</th><th>Было, ₽</th><th>Стало, ₽</th><th>Снижение</th></tr>'+drops+'</table></div>' if drops else '<p>нет данных</p>')+'</body></html>')
