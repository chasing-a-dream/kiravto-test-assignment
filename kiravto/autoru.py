"""Auto.ru's anonymous RIS application data, parsed without executing scripts."""
import hashlib
import itertools
import json
import re
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from kiravto.source import SourceFormatError,offer_id

BROWSER_HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'ru-RU,ru;q=0.9'}

class StateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active=False
        self.parts=[]
        self.chunks=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='script' and 'RIS' in attributes.get('class','').split() and attributes.get('type')=='application/json':
            self.active=True
            self.parts=[]
    def handle_data(self,data):
        if self.active: self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.active:
            self.chunks.append(''.join(self.parts))
            self.active=False

def application_state(html):
    parser=StateParser()
    parser.feed(html)
    if not 1<=len(parser.chunks)<=4:
        raise SourceFormatError('RIS state missing or fragmentation changed; no HTML-field fallback')
    # Script tags are streamed in different orders. Fragments are plain JSON,
    # not encrypted data. Bound permutations and parse; never eval source code.
    for order in itertools.permutations(parser.chunks):
        if not order[0].lstrip().startswith('{'): continue
        try:
            state=json.loads(''.join(order))
            if isinstance(state,dict) and ('listing' in state or 'card' in state): return state
        except ValueError: pass
    raise SourceFormatError('Cannot reconstruct RIS JSON: source format changed')

def listing_data(state):
    data=state.get('listing',{}).get('data',{})
    page=data.get('pagination',{})
    if not isinstance(data.get('offers'),list) or type(page.get('total_offers_count')) is not int or type(page.get('total_page_count')) is not int:
        raise SourceFormatError('Listing/pagination schema changed')
    return data

def epoch(value):
    if value in (None,''): return None
    try: return datetime.fromtimestamp(int(value)/1000,timezone.utc).isoformat()
    except (TypeError,ValueError,OverflowError,OSError): raise SourceFormatError('Invalid source timestamp') from None

COLORS={'000000':'чёрный','FAFBFB':'белый','FFFFFF':'белый','97948F':'серый','CACECB':'серебристый','0000CC':'синий','007F00':'зелёный','EE1D19':'красный','FF8649':'оранжевый','FFD600':'жёлтый','200204':'коричневый','C49648':'бежевый','660099':'фиолетовый','4A2197':'фиолетовый','22A0F8':'голубой'}
COLORS['040001']='чёрный'  # Confirmed against the visible Camry card in Edge.

def normalize(raw,detail=False):
    try:
        vehicle=raw['vehicle_info']
        docs=raw['documents']
        tech=vehicle['tech_param']
        config=vehicle['configuration']
        price=raw['price_info'].get('RUR',raw['price_info'].get('price'))
        if type(price) is not int: raise SourceFormatError('Rouble price missing or invalid')
        url=raw['url']
        identity=offer_id(url)
        if identity!=raw.get('saleId',raw.get('idHash')): raise SourceFormatError('Offer IDs disagree')
        if str(raw.get('id'))!=identity.split('-')[0]: raise SourceFormatError('Numeric ID disagrees with URL')
        if raw['section'] not in ('new','used'): raise SourceFormatError('Unknown source section')
        discount=raw.get('discount_options',{})
        max_discount=discount.get('max_discount',0)
        if type(max_discount) is not int or not 0<=max_discount<=price: raise SourceFormatError('Invalid maximum discount')
        photos=[]
        original_candidates=[]
        originals_verified=True
        for photo in raw['state'].get('image_urls',[]):
            sizes=photo.get('sizes',{})
            uri=sizes.get('orig') or sizes.get('1200x900')
            if not uri: raise SourceFormatError('Photo sizes changed')
            uri='https:'+uri if uri.startswith('//') else uri
            if not sizes.get('orig'):
                original_candidates.append(uri.rsplit('/',1)[0]+'/orig')
                originals_verified=False
            photos.append(uri)
        metadata=raw.get('additional_info',{})
        trim=vehicle.get('complectation',{})
        vin=docs.get('vin')
        description=raw.get('description')
        if detail and not isinstance(description,str): raise SourceFormatError('Card description missing')
        result={'id':identity,'source_id':str(raw['id']),'url':url,'name':raw.get('title'),'brand':vehicle['mark_info']['name'],'brand_code':vehicle['mark_info']['code'],'brand_aliases':[vehicle['mark_info'].get('ru_name')],'model':vehicle['model_info']['name'],'model_code':vehicle['model_info']['code'],'model_aliases':[vehicle['model_info'].get('ru_name')],'generation':vehicle['super_gen']['name'],'generation_id':vehicle['super_gen']['id'],'trim':trim.get('name'),'trim_id':trim.get('id'),'trim_not_published':not bool(trim),'year':docs['year'],'mileage':raw['state'].get('mileage'),'price':price,'discount_price':price-max_discount if max_discount else None,'discount_conditions':{k:v for k,v in discount.items() if k in ('tradein','insurance','credit','max_discount')},'engine':{'volume_l':round(tech.get('displacement',0)/1000,3),'power_hp':tech.get('power'),'fuel':tech.get('engine_type')},'transmission':tech.get('transmission'),'drive':tech.get('gear_type'),'body':config.get('body_type'),'color':COLORS.get(raw.get('color_hex','').upper(),raw.get('color_hex')),'color_hex':raw.get('color_hex'),'vin':vin,'vin_visibility':'hidden' if not vin else ('partial' if '*' in vin else 'full'),'owners':docs.get('owners_number'),'pts':docs.get('pts'),'customs':docs.get('custom_cleared'),'photos':photos,'photos_original_verified':False,'description':description,'posted_at':epoch(metadata.get('creation_date')),'raised_at':epoch(metadata.get('fresh_date')),'source_updated_at':epoch(metadata.get('update_date')),'availability':raw.get('availability'),'condition':raw['section'],'equipment':vehicle.get('equipment',{}),'source':'autoru_ris','dealer_id':raw.get('salon',{}).get('dealer_id'),'source_complete':bool(detail),'missing_fields':[]}
        if type(result['year']) is not int or type(result['mileage']) is not int:
            raise SourceFormatError('Year/mileage schema changed')
        result['photos_original_verified']=bool(photos) and originals_verified
        result['original_photo_candidates']=original_candidates
        result['engine']['volume_l']=round(tech['displacement']/1000,3) if tech.get('displacement') is not None else None
        result['missing_fields']=[key for key in ('trim','vin','owners','pts','customs','posted_at','raised_at','availability') if result[key] is None]
        if not result['photos_original_verified']: result['missing_fields'].append('verified_original_photos')
        if not photos: result['missing_fields'].append('photos')
        # The site displays its top owners-count bucket as "4 и более".
        result['owners_minimum']=isinstance(result['owners'],int) and result['owners']>=4
        return result
    except (KeyError,TypeError,AttributeError) as exc:
        raise SourceFormatError('Offer schema changed: '+str(exc)) from None

def signature(raw):
    row=normalize(raw)
    # Keep cache identity stable across presentation metadata fixes. The image
    # base identifies the same source photo independently of its rendition size.
    row.pop('original_photo_candidates',None)
    row.pop('owners_minimum',None)
    row['photos']=[url.rsplit('/',1)[0]+'/orig' for url in row['photos']]
    row['photos_original_verified']=False
    row['missing_fields']=[]
    if row['engine']['volume_l'] is None: row['engine']['volume_l']=0.0
    if row['color_hex']=='040001': row['color']='040001'
    return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
