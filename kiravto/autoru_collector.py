"""RIS collector. Category/filter verification is a separate completeness gate."""
import hashlib
import http.cookiejar
import json
import re
import time
from datetime import datetime,timezone
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urljoin,urlsplit
from urllib.request import Request,build_opener,HTTPCookieProcessor,HTTPRedirectHandler
from kiravto.autoru import BROWSER_HEADERS,COLORS,application_state,listing_data,normalize,signature
from kiravto.domain import Store
from kiravto.source import SourceFormatError,SourceTransportError,InventoryDriftError

BASE='https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/?damage_group=ANY&customs_state_group=DOESNT_MATTER'
MAX_IMAGE_BYTES=32_000_000

class ClientConfigParser(HTMLParser):
    """Read only the site's public transport config; never evaluate scripts."""
    def __init__(self):
        super().__init__(); self.active=None; self.parts=[]; self.values={}
    def handle_starttag(self,tag,attrs):
        name=dict(attrs).get('id')
        if name in ('app-config','app-config-client'):
            if self.active or name in self.values:raise SourceFormatError('Duplicate source client config')
            self.active=(tag,name);self.parts=[]
    def handle_data(self,data):
        if self.active:self.parts.append(data)
    def handle_endtag(self,tag):
        if self.active and tag==self.active[0]:
            try:value=json.loads(''.join(self.parts))
            except ValueError:raise SourceFormatError('Source client config JSON changed') from None
            if not isinstance(value,dict):raise SourceFormatError('Source client config schema changed')
            self.values[self.active[1]]=value;self.active=None

def client_config(html):
    parser=ClientConfigParser();parser.feed(html)
    config=parser.values.get('app-config',{})
    version=config.get('version')
    # A changed/foreign endpoint is a schema change, never an arbitrary fetch.
    if config.get('ajaxPath')!='/-/ajax/desktop-search/':
        raise SourceFormatError('Unexpected public source resource namespace')
    if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',version):
        raise SourceFormatError('Source app version missing or invalid')
    request_id=parser.values.get('app-config-client',{}).get('pageRequestId')
    if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[\x21-\x7e]{0,200}',request_id)):
        raise SourceFormatError('Source page request ID schema changed')
    return {'version':version,'ajaxPath':config['ajaxPath'],'pageRequestId':request_id}

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        from kiravto.photos import original_url
        old,new=urlsplit(req.full_url),urlsplit(newurl)
        try:
            permitted=(old.hostname=='auto.ru' and new.scheme=='https' and new.hostname=='auto.ru' and new.port in (None,443) and not new.username and not new.password)
            if old.hostname!='auto.ru': permitted=original_url(newurl)==newurl
        except (ValueError,SourceFormatError): permitted=False
        if not permitted: raise SourceFormatError('Source redirect destination is not permitted')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def unrestricted_filters(parameters):
    """Fail closed on new filter keys or a narrower returned search."""
    expected={'cars_params':{'body_type_group':['ANY_BODY'],'seats_group':'ANY_SEATS','engine_group':['ANY_ENGINE']},'currency':'RUR','in_stock':'ANY_STOCK','dealer_id':'20866489','offer_grouping':False,'with_discount':True,'with_delivery':'BOTH','dealer_ids':['20866489'],'seller_group':['ANY_SELLER'],'damage_group':'ANY','owners_count_group':'ANY_COUNT','owning_time_group':'ANY_TIME','customs_state_group':'DOESNT_MATTER'}
    allowed=set(expected)|{'state_group','has_image','search_tag'}
    return (isinstance(parameters,dict) and not set(parameters)-allowed and all(parameters.get(key)==value for key,value in expected.items()) and parameters.get('state_group') in ('ALL','USED','NEW') and type(parameters.get('has_image')) is bool and not parameters.get('search_tag'))

def save_cache(path,cache):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)

def gate_parameters(state):
    """Mirror the public frontend's RIS geo -> GateApi configuration.

    The observed dealer profile has no enabled radius. Do not silently narrow
    inventory if that changes, or substitute a guessed/default region.
    """
    geo=state.get('geo')
    if not isinstance(geo,dict): raise SourceFormatError('Anonymous bootstrap geo configuration missing')
    gids=geo.get('gids')
    if not isinstance(gids,list) or not gids or any(type(value) is not int or value<=0 for value in gids):
        raise SourceFormatError('Anonymous bootstrap geo IDs changed schema')
    if geo.get('radiusAllowed') is not False:
        raise SourceFormatError('Disabled geographic radius not confirmed for unrestricted dealer search')
    return {'geo_id':gids[:]}

class PublicSource:
    def __init__(self,pause=3):
        self.pause=pause
        self.requests=0
        self.cookies=http.cookiejar.CookieJar()
        self.opener=build_opener(HTTPCookieProcessor(self.cookies),SafeRedirect())
        self.search_parameters=None
        self.gate_parameters={}
        self.client_config={}
        self.referer=BASE
        self.last_request={}
    def wait_for_host(self,url):
        host=urlsplit(url).hostname
        delay=self.pause-(time.monotonic()-self.last_request.get(host,float('-inf')))
        if delay>0: time.sleep(delay)
        self.last_request[host]=time.monotonic()
    def listing(self,page):
        if self.search_parameters is None:
            self.referer,state=self.get(BASE)
            data=listing_data(state)
            self.gate_parameters=gate_parameters(state)
            self.search_parameters={**state.get('router',{}).get('params',{}),**data.get('search_parameters',{})}
            if self.search_parameters.get('dealer_id')!='20866489' or self.search_parameters.get('category')!='cars':
                raise SourceFormatError('Anonymous listing bootstrap has unexpected dealer/category')
        data=self.request_listing('all',page)
        if not data['offers'] and data['pagination']['total_offers_count']>0:
            # One ordinary retry for a transient successful-but-empty response.
            # HTTP/protection errors are never retried here.
            data=self.request_listing('all',page)
        return self.referer,{'listing':{'data':data}}

    def request_listing(self,section,page):
        """Site's observed desktop-search request, with our own anonymous session."""
        parameters={**self.search_parameters,'section':section,'page':page}
        data=self.api_request('listing',parameters)
        if data.get('status')!='SUCCESS': raise SourceFormatError('Source API did not return SUCCESS')
        listing_data({'listing':{'data':data}})
        parameters=data.get('original_search_parameters',{})
        if not unrestricted_filters(parameters) or parameters.get('state_group')!=section.upper():
            raise SourceFormatError('Source returned a restricted or different category')
        return data

    def api_request(self,name,parameters):
        if name not in ('listing','getOfferCard'): raise ValueError('Unsupported source resource')
        parameters=dict(parameters)
        # GateApi adds this to every resource, after the resource payload merge.
        # Preserve an explicit geo_id/geo_id[] exactly as the site's client does.
        if 'geo_id' not in parameters and 'geo_id[]' not in parameters:
            parameters.update(self.gate_parameters)
        csrf=next((cookie.value for cookie in self.cookies if cookie.name=='_csrf_token'),None)
        if not csrf: raise SourceFormatError('Anonymous source session has no CSRF token')
        headers={**BROWSER_HEADERS,'Accept':'application/json','Content-Type':'application/json','X-CSRF-Token':csrf,'X-Requested-With':'XMLHttpRequest','Referer':self.referer,'Origin':'https://auto.ru'}
        if self.client_config:
            headers.update({'X-Client-App-Version':self.client_config['version'],'X-Client-Date':str(int(time.time()*1000)),'X-Retpath-Y':self.referer})
            if self.client_config.get('pageRequestId'):
                headers['X-Page-Request-Id']=self.client_config['pageRequestId']
        for attempt in range(2):
            self.wait_for_host('https://auto.ru/')
            self.requests+=1
            try:
                request=Request('https://auto.ru'+self.client_config.get('ajaxPath','/-/ajax/desktop-search/')+name+'/',data=json.dumps(parameters,separators=(',',':'),ensure_ascii=False).encode('utf-8'),headers=headers,method='POST')
                with self.opener.open(request,timeout=30) as response: data=json.load(response)
                break
            except HTTPError as exc:
                raise SourceFormatError('Source API HTTP '+str(exc.code)+'; no protection bypass') from None
            except (URLError,OSError,HTTPException):
                # One rate-limited retry for transport failures only. HTTP errors,
                # HTML/protection pages and schema changes are never retried.
                if attempt==0: continue
                raise SourceTransportError('Source API transport unavailable after one retry') from None
            except ValueError:
                raise SourceFormatError('Source API returned invalid JSON; no retry') from None
        if not isinstance(data,dict) or data.get('error'):
            raise SourceFormatError('Source API returned an error or changed schema')
        return data

    def card(self,identity):
        sale_id,sale_hash=identity.split('-',1)
        raw=self.api_request('getOfferCard',{'sale_id':sale_id,'sale_hash':sale_hash,'category':'cars'})
        if raw.get('status') in ('INACTIVE','REMOVED'):
            raise SourceFormatError('Source card unpublished during scan: '+identity)
        if not raw.get('vehicle_info'): raise SourceFormatError('Source card schema changed')
        return raw

    def confirm_missing(self,identity):
        """Require an explicit matching unpublished card, never infer a sale.

        INACTIVE was confirmed against four public cards in Edge: direct-link
        access only. REMOVED is also an offer-status enum in the public bundle.
        Errors, unknown states, and a missing identity remain unconfirmed.
        """
        sale_id,sale_hash=identity.split('-',1)
        raw=self.api_request('getOfferCard',{'sale_id':sale_id,'sale_hash':sale_hash,'category':'cars'})
        identities=[raw[key] for key in ('saleId','idHash') if raw.get(key) is not None]
        same=bool(identities) and all(value==identity for value in identities)
        if raw.get('id') is not None: same=same and str(raw['id'])==sale_id
        return {'id':identity,'confirmed':same and raw.get('status') in ('INACTIVE','REMOVED'),'identity_matches':same,'status':raw.get('status'),'checked_at':datetime.now(timezone.utc).isoformat()}

    def image(self,url):
        from kiravto.photos import original_url
        if original_url(url)!=url: raise SourceFormatError('Only original source image URLs may be requested')
        for attempt in range(2):
            self.wait_for_host(url)
            self.requests+=1
            try:
                with self.opener.open(Request(url,headers=BROWSER_HEADERS),timeout=20) as response:
                    content=response.read(MAX_IMAGE_BYTES+1)
                    content_type=response.headers.get('Content-Type','').split(';')[0]
                break
            except HTTPError as exc: raise SourceFormatError('Source image HTTP '+str(exc.code)+'; no retry') from None
            except (URLError,OSError,HTTPException):
                if attempt==0: continue
                raise SourceTransportError('Source image unavailable after one retry') from None
        if len(content)>MAX_IMAGE_BYTES: raise SourceFormatError('Source original exceeds 32 MB limit')
        if not content_type.startswith('image/'):
            raise SourceFormatError('Source original has an unsupported content type')
        return content,content_type

    def category_counts(self):
        result={}
        for section,key in [('new','newOffersCount'),('used','usedOffersCount')]:
            data=self.request_listing(section,1)
            expected='NEW' if section=='new' else 'USED'
            parameters=data.get('original_search_parameters',{})
            if not unrestricted_filters(parameters) or parameters.get('state_group')!=expected:
                raise SourceFormatError('Category query returned a restricted or different section')
            result[key]=data['pagination']['total_offers_count']
        return result

    def verify_inventory(self,observed,parameters,first_page,expected,max_pages=30):
        """Repeat complete ALL/USED manifests; never relax NEW's narrowed filters.

        This checks observed set consistency within a time window, not an atomic
        snapshot or an independent unrestricted NEW endpoint.
        """
        if not unrestricted_filters(parameters) or parameters.get('state_group')!='ALL' or len(observed)!=expected or any(value not in ('new','used') for value in observed.values()):
            raise SourceFormatError('Observed inventory cannot be partitioned')
        neutral={key:value for key,value in parameters.items() if key!='state_group'}
        evidence=[]

        def checked_page(section,page):
            data=self.request_listing(section,page)
            returned=data.get('original_search_parameters',{})
            if not unrestricted_filters(returned) or returned.get('state_group')!=section.upper() or {key:value for key,value in returned.items() if key!='state_group'}!=neutral:
                raise SourceFormatError('Manifest filters differ from initial ALL search')
            pagination=data['pagination']
            if pagination.get('page')!=page: raise SourceFormatError('Manifest page reset')
            rows={}
            for raw in data['offers']:
                row=normalize(raw)
                if row['dealer_id']!='20866489' or section=='used' and row['condition']!='used':
                    raise SourceFormatError('Manifest contains another dealer or category')
                if row['id'] in rows: raise SourceFormatError('Manifest contains duplicate IDs')
                rows[row['id']]=row['condition']
            evidence.append({'section':section,'page':page,'checked_at':datetime.now(timezone.utc).isoformat(),'parameters':returned,'total':pagination['total_offers_count'],'total_pages':pagination['total_page_count'],'ids':list(rows),'sections':dict(rows)})
            return rows,pagination

        def manifest(section):
            result={}
            total=None
            pages=None
            first=None
            for page in range(1,max_pages+1):
                rows,pagination=checked_page(section,page)
                if total is None:
                    total=pagination['total_offers_count']; pages=max(1,pagination['total_page_count']); first=set(rows)
                    if pages>max_pages: raise SourceFormatError('Manifest exceeds page limit')
                if pagination['total_offers_count']!=total or max(1,pagination['total_page_count'])!=pages:
                    raise SourceFormatError('Manifest counts changed during verification')
                if set(rows)&set(result): raise SourceFormatError('Manifest repeats IDs across pages')
                if not rows and total: raise SourceFormatError('Manifest has an unexpected empty page')
                result.update(rows)
                if page>=pages: break
            if len(result)!=total: raise SourceFormatError('Manifest unique IDs disagree with total')
            return result,first

        all_rows,all_first=manifest('all')
        if all_rows!=observed or all_first!=set(first_page):
            raise SourceFormatError('ALL inventory changed since card collection')
        used_rows,_=manifest('used')
        expected_used={identity:section for identity,section in observed.items() if section=='used'}
        if used_rows!=expected_used:
            raise SourceFormatError('USED inventory does not match ALL partition')
        final,pagination=checked_page('all',1)
        if final!={identity:all_rows[identity] for identity in all_first} or pagination['total_offers_count']!=expected:
            raise SourceFormatError('ALL inventory changed after category verification')
        return {'verified':True,'method':'all_used_manifest_partition','atomic_snapshot':False,'new_count_basis':'ALL IDs minus independently enumerated USED IDs','counts':{'newOffersCount':len(observed)-len(used_rows),'usedOffersCount':len(used_rows)},'evidence':evidence}
    def get(self,url):
        for redirect in range(3):
            self.wait_for_host(url)
            self.requests+=1
            try:
                with self.opener.open(Request(url,headers=BROWSER_HEADERS),timeout=30) as response:
                    html=response.read().decode('utf-8')
                    final=response.url
            except HTTPError as exc:
                raise SourceFormatError('Source HTTP '+str(exc.code)+'; no protection bypass') from None
            except (URLError,OSError,HTTPException):
                raise SourceFormatError('Source unavailable; retry at next scheduled run') from None
            try:
                state=application_state(html)
                self.client_config=client_config(html)
                return final,state
            except SourceFormatError:
                match=re.search(r'<meta\b[^>]*http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)',html,re.I)
                if not match: raise
                target=re.search(r'url=(.*)',unescape(match[1]),re.I)
                if not target: raise
                destination=urljoin(final,target[1])
                parts=urlsplit(destination)
                if parts.scheme!='https' or parts.hostname!='auto.ru' or not parts.path.startswith(('/diler/cars/','/cars/')) or destination==url:
                    raise SourceFormatError('Source redirect loop or unsupported destination') from None
                url=destination
        raise SourceFormatError('Too many source canonical redirects')

def collect(db_path,max_pages=30,fetcher=None,max_details=None,notify=True,max_drift_retries=1):
    if type(max_drift_retries) is not int or max_drift_retries not in (0,1):
        raise ValueError('max_drift_retries must be 0 or 1')
    source=fetcher or PublicSource()
    request_offset=source.requests
    inventory_drift=False
    Path(db_path).parent.mkdir(parents=True,exist_ok=True)
    store=Store(db_path)
    cache_file=Path(db_path).parent/'ris-cache.json'
    cache=json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
    for entry in cache.values():
        card=entry['card']
        card['color']=COLORS.get(str(card.get('color_hex','')).upper(),card.get('color_hex'))
        card['owners_minimum']=isinstance(card.get('owners'),int) and card['owners']>=4
        if 'original_photo_candidates' not in card and card.get('photos_original_verified') is False:
            # Older cache guessed /orig from an explicitly published 1200x900 size.
            # Restore the published URLs without a second network request.
            card['original_photo_candidates']=card.get('photos',[])[:]
            card['photos']=[url.rsplit('/',1)[0]+'/1200x900' for url in card.get('photos',[])]
            card['missing_fields']=[key for key in ('trim','vin','owners','pts','customs','posted_at','raised_at','availability') if card.get(key) is None]+['verified_original_photos']
            if not card['photos']: card['missing_fields'].append('photos')
    started=time.monotonic()
    observed=datetime.now(timezone.utc).isoformat()
    records={}
    seen=set()
    errors=[]
    expected=None
    details=0
    listing_complete=False
    filters_verified=False
    counts={}
    sections={'new':0,'used':0}
    category_verified=False
    page_counts=[]
    listed=[]
    listing_evidence={}
    detail_evidence={}
    detail_transport_failures=[]
    phases={'listing_started_at':observed}
    manifest={}
    initial_parameters=None
    first_page=[]
    category_proof=None
    partition_evidence=None
    from kiravto.photos import PhotoEvidence
    photos=PhotoEvidence(Path(db_path).parent/'image-evidence.json',source.image) if hasattr(source,'image') else None
    try:
        if hasattr(source,'inventory_manifest'):
            inventory=source.inventory_manifest('all')
            expected=inventory['total'];initial_parameters=inventory['parameters']
            partition_evidence=inventory['evidence']
            if not unrestricted_filters(initial_parameters) or initial_parameters.get('state_group')!='ALL':
                raise SourceFormatError('Initial price partition filters are not unrestricted ALL')
            for observation in inventory['observations']:
                raw=observation['raw'];row=normalize(raw)
                if row['id'] in seen or row['dealer_id']!='20866489':
                    raise SourceFormatError('Price manifest contains duplicate or foreign identity')
                seen.add(row['id']);manifest[row['id']]=row['condition'];sections[row['condition']]+=1
                listed.append(raw)
                listing_evidence[row['id']]={'signature':signature(raw),'condition':row['condition'],'checked_at':observation['checked_at'],'partition_bounds':observation['bounds']}
            if len(seen)!=expected or manifest!=inventory['identities']:
                raise SourceFormatError('Initial price manifest union changed')
            page_counts=[{key:node[key] for key in ('bounds','total','total_pages','checked_at')} for node in partition_evidence if node['leaf']]
            listing_complete=True;filters_verified=True
        for page in ([] if hasattr(source,'inventory_manifest') else range(1,max_pages+1)):
            if hasattr(source,'listing'):
                _,state=source.listing(page)
            else:
                _,state=source.get(BASE+('&page='+str(page) if page>1 else ''))
            data=listing_data(state)
            pagination=data['pagination']
            reported=pagination['total_offers_count']
            if expected is not None and reported!=expected:
                raise SourceFormatError('Inventory count changed during card collection')
            expected=reported if expected is None else max(expected,reported)
            if pagination.get('page')!=page:
                raise SourceFormatError('Pagination reset or unexpected page')
            active_filters=data.get('original_search_parameters',{})
            if page==1: initial_parameters=active_filters
            elif active_filters!=initial_parameters: raise SourceFormatError('Listing filters changed between pages')
            # These exact values were produced by the visible Edge UI. The
            # "С фото" checkbox maps to search_tag=real_photo, not has_image.
            page_verified=unrestricted_filters(active_filters)
            if hasattr(source,'listing'): page_verified=page_verified and active_filters.get('state_group')=='ALL'
            filters_verified=page_verified if page==1 else filters_verified and page_verified
            counts=state.get('salonInfo',{}).get('salonUsedNewOffersCounters',{})
            fresh=[]
            checked_at=datetime.now(timezone.utc).isoformat()
            for raw in data['offers']:
                row=normalize(raw)
                if row['dealer_id']!='20866489': raise SourceFormatError('Offer belongs to another dealer')
                if row['id'] in seen: continue
                seen.add(row['id'])
                manifest[row['id']]=row['condition']
                if page==1: first_page.append(row['id'])
                sections[row['condition']]+=1
                fresh.append(raw)
                listing_evidence[row['id']]={'signature':signature(raw),'condition':row['condition'],'checked_at':checked_at}
            page_counts.append({'page':page,'offers':len(data['offers']),'new_ids':len(fresh),'checked_at':checked_at})
            if not data['offers'] and (page<pagination['total_page_count'] or len(seen)<reported):
                raise SourceFormatError('Unexpected empty page before reported inventory was reached')
            if not fresh and data['offers']:
                raise SourceFormatError('Repeated page; inventory may have shifted')
            listed.extend(fresh)
            if page>=pagination['total_page_count']:
                listing_complete=True
                break
        # Enumerate the inventory before slow detail/photo downloads. This keeps
        # the listing window short; the final independent manifests still gate
        # completeness and reject changes during the detail phase.
        phases['listing_finished_at']=datetime.now(timezone.utc).isoformat()
        phases['details_started_at']=phases['listing_finished_at']
        for raw in listed:
            detail_phase='card'
            row=None
            try:
                row=normalize(raw)
                stamp=signature(raw)
                entry=cache.get(row['id'])
                reused=bool(entry and entry['signature']==stamp)
                if reused:
                    card=entry['card']
                else:
                    if max_details is not None and details>=max_details: continue
                    if hasattr(source,'card'):
                        raw_card=source.card(row['id'])
                    else:
                        _,card_state=source.get(row['url'])
                        raw_card=card_state.get('card',{})
                    details+=1
                    card=normalize(raw_card,detail=True)
                if any(card[key]!=row[key] for key in ('id','dealer_id','condition')):
                    raise SourceFormatError('Card/listing identity, dealer or category mismatch')
                if not reused:
                    cache[row['id']]={'signature':stamp,'card':card,'fetched_at':datetime.now(timezone.utc).isoformat()}
                    # Checkpoint each validated card so a stopped cold scan can resume.
                    save_cache(cache_file,cache)
                detail_evidence[row['id']]={'cache_reused':reused,'fetched_at':cache[row['id']].get('fetched_at')}
                detail_phase='photo'
                if photos is not None:
                    try: card=photos.enrich(card)
                    except SourceTransportError: raise
                    except SourceFormatError as exc: raise SourceFormatError('Photo '+row['id']+': '+str(exc)) from None
                    cache[row['id']]['card']=card
                    save_cache(cache_file,cache)
                anomalies=[]
                if card['price']<=1: anomalies.append('suspicious_price')
                if card['condition']=='used' and card['mileage']==0: anomalies.append('used_zero_mileage')
                if card['year']>datetime.now(timezone.utc).year: anomalies.append('future_year')
                card=dict(card,anomalies=anomalies)
                records[card['id']]=card
            except SourceTransportError as exc:
                failure={'id':row['id'],'phase':detail_phase,'error':str(exc)}
                detail_transport_failures.append(failure)
                errors.append(detail_phase+' '+row['id']+': '+str(exc))
                if len(detail_transport_failures)>=3:
                    raise SourceTransportError('Detail transport failure limit reached (3)') from None
                continue
            except SourceFormatError as exc:
                identity=row['id'] if row else raw.get('saleId',raw.get('idHash','unknown'))
                raise SourceFormatError(detail_phase+' '+str(identity)+': '+str(exc)) from None
        phases['details_finished_at']=datetime.now(timezone.utc).isoformat()
        phases['verification_started_at']=phases['details_finished_at']
        if listing_complete and hasattr(source,'verify_inventory'):
            category_proof=source.verify_inventory(manifest,initial_parameters,first_page,expected,max_pages)
            counts=category_proof['counts']
            category_verified=(category_proof.get('verified') is True and counts.get('newOffersCount')==sections['new'] and counts.get('usedOffersCount')==sections['used'] and sum(counts.values())==len(seen)==expected)
        elif listing_complete and hasattr(source,'category_counts'):
            counts=source.category_counts()
            category_verified=(counts.get('newOffersCount')==sections['new'] and counts.get('usedOffersCount')==sections['used'] and sum(counts.values())==len(seen)==expected)
    except SourceFormatError as exc:
        inventory_drift=isinstance(exc,InventoryDriftError)
        errors.append(str(exc))
    except BaseException:
        store.close()
        raise
    try:
        limitations=[]
        if not filters_verified: limitations.append('unfiltered_inventory_not_verified')
        if not listing_complete or len(seen)<(expected or 0): limitations.append('pagination_or_inventory_shift')
        if len(records)<len(seen): limitations.append('detail_limit_or_unavailable_cards')
        if not category_verified: limitations.append('category_coverage_not_verified')
        inventory_complete=listing_complete and filters_verified and category_verified
        details_complete=len(records)==len(seen)
        photos_verified=bool(records) and all(row.get('photos_original_verified') for row in records.values())
        if not photos_verified: limitations.append('original_photos_not_verified')
        # Missing IDs require a separate detail confirmation before any removal.
        missing={row['id'] for row in store.search(limit=10000)}-seen
        confirmations=[]
        confirmed_missing=set()
        if inventory_complete and details_complete and photos_verified and not errors and hasattr(source,'confirm_missing'):
            for identity in sorted(missing):
                try:
                    evidence=source.confirm_missing(identity)
                    confirmations.append(evidence)
                    if evidence.get('id')==identity and evidence.get('confirmed') is True: confirmed_missing.add(identity)
                except SourceFormatError as exc:
                    errors.append('Missing offer confirmation: '+str(exc))
                    break
        if inventory_complete and missing-confirmed_missing:
            limitations.append('missing_offer_confirmation_required')
        from kiravto.anomalies import mark_model_price_anomalies
        mark_model_price_anomalies(records)
        complete=inventory_complete and details_complete and photos_verified and not errors and not (missing-confirmed_missing)
        finished=datetime.now(timezone.utc).isoformat()
        run=store.ingest(list(records.values()),complete=complete,expected_count=expected,confirmed_missing=confirmed_missing,missing_evidence={row['id']:row for row in confirmations if row['id'] in confirmed_missing},now=finished)
        result={**run,'adapter':'autoru_ris_api','requests':source.requests-request_offset,'details_fetched':details,'photos_fetched':photos.fetched if photos else 0,'seen_ids':len(seen),'duration_seconds':round(time.monotonic()-started,2),'errors':errors,'limitations':limitations,'filters_verified':filters_verified,'category_counts':counts,'category_counts_verified':category_verified,'inventory_complete':inventory_complete,'details_complete':details_complete,'sections_seen':sections,'pages':page_counts,'inventory_drift':inventory_drift}
        result['missing_confirmations']=confirmations
        result['category_proof']=category_proof
        result['started_at']=observed
        result['finished_at']=finished
        result['phases']=phases
        result['listing_evidence']=listing_evidence
        if partition_evidence is not None:
            result['adapter']='autoru_ris_price_partitions'
            result['partition_evidence']=partition_evidence
        result['detail_evidence']=detail_evidence
        result['detail_transport_failures']=detail_transport_failures
        result['default_gate_context']=dict(getattr(source,'gate_parameters',{}))
        profile=getattr(source,'client_config',{})
        result['client_profile']={'app_version':profile.get('version'),'ajax_path':profile.get('ajaxPath'),'page_request_id_present':bool(profile.get('pageRequestId'))}
        from kiravto.observability import record
        record(db_path,result,notify=notify)
        save_cache(cache_file,cache)
        Path(db_path).with_suffix('.last-run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    finally: store.close()
    if inventory_drift and len(errors)==1 and max_drift_retries and hasattr(source,'inventory_manifest'):
        # Keep the failed attempt as immutable history. Restart enumeration with
        # empty sets; only validated card/photo cache is reused, never old IDs.
        retry_started=time.monotonic()
        time.sleep(max(3,getattr(source,'pause',3)))
        retried=collect(db_path,max_pages,source,max_details,notify,max_drift_retries=0)
        keys=('id','complete','count','requests','duration_seconds','errors')
        retried['collection_attempts']=[{key:row[key] for key in keys} for row in (result,retried)]
        retried['collection_total_requests']=result['requests']+retried['requests']
        retried['collection_total_seconds']=round(result['duration_seconds']+time.monotonic()-retry_started,2)
        record(db_path,retried,notify=False)
        Path(db_path).with_suffix('.last-run.json').write_text(json.dumps(retried,ensure_ascii=False,indent=2),encoding='utf-8')
        return retried
    return result
