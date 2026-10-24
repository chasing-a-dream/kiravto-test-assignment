"""Public price partitions, proved by counts, disjoint IDs and native filters.

The observed filter uses inclusive integer RUR prices after maximum discounts.
Every bound and row is checked again; a changed contract fails closed.
"""
import time
from datetime import datetime,timezone
from kiravto.autoru import listing_data,normalize
from kiravto.autoru_collector import PublicSource,BASE,gate_parameters,unrestricted_filters
from kiravto.source import SourceFormatError,InventoryDriftError

NATIVE_COLORS=('040001','CACECB','FAFBFB','97948F','0000CC','EE1D19','007F00','200204','C49648','22A0F8','DEA522','660099','4A2197','FFD600','FF8649','FFC0CB')

def price_manifest(fetch,section,max_requests=96,max_seconds=360,max_depth=32,max_tie_pages=4):
    if section not in ('all','used'):raise ValueError('Unsupported partition section')
    started=time.monotonic();evidence=[];observations=[];identities={};neutral=None

    def read(bounds,depth,page=1):
        nonlocal neutral
        if depth>max_depth or len(evidence)>=max_requests or time.monotonic()-started>max_seconds:
            raise SourceFormatError('Price partition resource limit reached')
        data=fetch(section,bounds) if page==1 else fetch(section,bounds,page)
        if data.get('status')!='SUCCESS':raise SourceFormatError('Price partition response is not SUCCESS')
        listing_data({'listing':{'data':data}})
        parameters=dict(data.get('original_search_parameters',{}))
        echoed={key:parameters.pop(key) for key in ('price_from','price_to','color') if key in parameters}
        prices={key:value for key,value in echoed.items() if key!='color'}
        if prices!={key:value for key,value in bounds.items() if key!='color'} or any(type(value) is not int for value in prices.values()):
            raise SourceFormatError('Price partition bounds not confirmed')
        if 'color' in bounds:
            colors=echoed.get('color')
            if not isinstance(colors,list) or any(type(value) is not str or value not in NATIVE_COLORS for value in colors) or len(set(colors))!=len(colors) or set(colors)!=set(bounds['color']):
                raise SourceFormatError('Price partition colors not confirmed')
        elif 'color' in echoed:raise SourceFormatError('Unexpected color restriction')
        if not unrestricted_filters(parameters) or parameters.get('state_group')!=section.upper():
            raise SourceFormatError('Price partition neutral filters changed')
        if neutral is None:neutral=parameters
        elif parameters!=neutral:raise SourceFormatError('Price partition filters drifted')
        pagination=data['pagination'];total=pagination['total_offers_count'];pages=pagination['total_page_count']
        if pagination.get('page')!=page or total<0 or pages<0 or (total>0 and pages<1):
            raise SourceFormatError('Price partition pagination changed')
        checked_at=datetime.now(timezone.utc).isoformat();rows=[]
        for raw in data['offers']:
            row=normalize(raw)
            if row['dealer_id']!='20866489' or section=='used' and row['condition']!='used':
                raise SourceFormatError('Price partition dealer/category changed')
            price=row['discount_price'] if row['discount_price'] is not None else row['price']
            if price<bounds.get('price_from',0) or ('price_to' in bounds and price>bounds['price_to']):
                raise SourceFormatError('Offer is outside confirmed discounted-price partition')
            if 'color' in bounds and row['color_hex'] not in bounds['color']:
                raise SourceFormatError('Offer is outside confirmed color partition')
            rows.append((raw,row,price))
        node={'section':section,'bounds':dict(bounds),'parameters':{**parameters,**echoed},'total':total,'total_pages':pages,'page':page,'checked_at':checked_at,'leaf':False}
        evidence.append(node)
        return node,rows

    def reconcile_count(node,bounds,depth,children_total,subtree_ids):
        # A counter read before the children can become stale during traversal.
        # Re-read the same validated query; never just discard the old counter.
        fresh,rows=read(bounds,depth)
        fresh['kind']='partition_count_recheck'
        if fresh['total']!=children_total or len(subtree_ids)!=children_total:
            raise InventoryDriftError('Price partition children do not cover parent count',
                                      section=section,bounds=bounds,before=node['total'],
                                      after=fresh['total'],children=children_total)
        fresh_ids=[row['id'] for _,row,_ in rows]
        fresh['ids']=fresh_ids
        fresh['sections']={row['id']:row['condition'] for _,row,_ in rows}
        if (fresh['total'] and not rows) or len(fresh_ids)!=len(set(fresh_ids)):
            raise InventoryDriftError('Price partition recheck has empty or duplicate rows',section=section,bounds=bounds)
        if any(identities.get(row['id'])!=row['condition'] or row['id'] not in subtree_ids for _,row,_ in rows):
            raise InventoryDriftError('Price partition recheck contains unenumerated IDs',section=section,bounds=bounds)
        node['original_total']=node['total']
        node['total']=fresh['total']
        node['rechecked_at']=fresh['checked_at']
        node['subtree_unique_count']=len(subtree_ids)
        return fresh['total']

    def request(bounds,depth):
        previous_ids=set(identities)
        node,rows=read(bounds,depth);total=node['total'];pages=node['total_pages']
        observation_times={row['id']:node['checked_at'] for _,row,_ in rows}
        lower=bounds.get('price_from',0);upper=bounds.get('price_to')
        exact_price=upper is not None and lower==upper
        if exact_price and pages>1 and len(bounds.get('color',NATIVE_COLORS))>1:
            colors=list(bounds.get('color',NATIVE_COLORS));middle=len(colors)//2
            node['split_colors']=[colors[:middle],colors[middle:]]
            left_total=request({**bounds,'color':colors[:middle]},depth+1)
            right_total=request({**bounds,'color':colors[middle:]},depth+1)
            if left_total+right_total!=total:
                return reconcile_count(node,bounds,depth,left_total+right_total,set(identities)-previous_ids)
            return total
        if exact_price and pages>max_tie_pages:
            raise SourceFormatError('Price tie exceeds bounded pagination capacity')
        if exact_price and pages>1:
            for page in range(2,pages+1):
                extra,more=read(bounds,depth,page)
                if extra['total']!=total or extra['total_pages']!=pages:
                    raise InventoryDriftError('Exact-price pagination counts changed',section=section,bounds=bounds,before=total,after=extra['total'])
                extra['kind']='exact_price_page'
                extra['ids']=[row['id'] for _,row,_ in more]
                observation_times.update({row['id']:extra['checked_at'] for _,row,_ in more})
                rows.extend(more)
        if pages<=1 or exact_price:
            node['leaf']=True
            ids=[row['id'] for _,row,_ in rows]
            if len(ids)!=total or len(set(ids))!=total or set(ids)&set(identities):
                raise InventoryDriftError('Price partition leaf count or unique IDs disagree',section=section,bounds=bounds,expected=total,rows=len(ids),unique=len(set(ids)),overlap=len(set(ids)&set(identities)))
            node['ids']=ids;node['sections']={row['id']:row['condition'] for _,row,_ in rows}
            identities.update(node['sections'])
            observations.extend({'raw':raw,'checked_at':observation_times[row['id']],'bounds':dict(bounds)} for raw,row,_ in rows)
            return total
        if not rows:raise InventoryDriftError('Price partition is unexpectedly empty',section=section,bounds=bounds,expected=total)
        prices=sorted(set(price for _,_,price in rows));pivot=prices[len(prices)//2]
        if upper is not None and pivot>=upper:
            # Isolate the observed boundary price directly; don't bisect empty
            # ruble ranges repeatedly when many cars share the same price.
            pivot=upper-1
        if pivot<lower or upper is not None and pivot>=upper:
            raise SourceFormatError('Price tie exceeds one-page partition capacity')
        left={**bounds,'price_to':pivot};right={**bounds,'price_from':pivot+1}
        node['split_at']=pivot
        left_total=request(left,depth+1);right_total=request(right,depth+1)
        if left_total+right_total!=total:
            return reconcile_count(node,bounds,depth,left_total+right_total,set(identities)-previous_ids)
        return total

    total=request({},0)
    if len(identities)!=total:raise SourceFormatError('Price partition union disagrees with root count')
    return {'total':total,'identities':identities,'observations':observations,'parameters':neutral,'evidence':evidence,'atomic_snapshot':False}


class PartitionedPublicSource(PublicSource):
    def __init__(self,pause=3,max_tie_pages=4):
        super().__init__(pause)
        if type(max_tie_pages) is not int or not 1<=max_tie_pages<=4:
            raise ValueError('max_tie_pages must be 1..4')
        self.max_tie_pages=max_tie_pages

    def ensure_bootstrap(self):
        if self.search_parameters is None:
            self.referer,state=self.get(BASE)
            self.gate_parameters=gate_parameters(state)
            self.search_parameters={**state.get('router',{}).get('params',{}),**listing_data(state).get('search_parameters',{})}
            if self.search_parameters.get('dealer_id')!='20866489' or self.search_parameters.get('category')!='cars':
                raise SourceFormatError('Partition bootstrap dealer/category changed')

    def fetch_price_listing(self,section,bounds,page=1):
        self.ensure_bootstrap()
        return self.api_request('listing',{**self.search_parameters,'section':section,'page':page,**bounds})

    def inventory_manifest(self,section='all'):
        return price_manifest(self.fetch_price_listing,section,max_tie_pages=self.max_tie_pages)

    def verify_inventory(self,observed,parameters,first_page,expected,max_pages=30):
        all_rows=self.inventory_manifest('all')
        if all_rows['parameters']!=parameters:
            raise SourceFormatError('Partition ALL neutral filters changed')
        if all_rows['identities']!=observed or all_rows['total']!=expected:
            raise InventoryDriftError('Partition ALL inventory changed since card collection',before=expected,after=all_rows['total'],added=len(set(all_rows['identities'])-set(observed)),missing=len(set(observed)-set(all_rows['identities'])))
        used=self.inventory_manifest('used')
        neutral=lambda value:{key:item for key,item in value.items() if key!='state_group'}
        if neutral(used['parameters'])!=neutral(parameters):
            raise SourceFormatError('Partition USED neutral filters changed')
        if used['identities']!={key:value for key,value in observed.items() if value=='used'}:
            raise InventoryDriftError('Partition USED inventory disagrees with ALL')
        # Re-read unrestricted root after the independent USED manifest.
        final=self.fetch_price_listing('all',{})
        if final.get('status')!='SUCCESS' or final.get('original_search_parameters')!=parameters:
            raise SourceFormatError('Partition root changed after verification')
        listing_data({'listing':{'data':final}})
        if final.get('pagination',{}).get('total_offers_count')!=expected:
            raise InventoryDriftError('Partition root count changed after verification',before=expected,after=final.get('pagination',{}).get('total_offers_count'))
        final_rows=[normalize(raw) for raw in final['offers']]
        pagination=final['pagination']
        if pagination.get('page')!=1 or pagination['total_page_count']<0 or expected>0 and pagination['total_page_count']<1 or not final_rows and expected or len({row['id'] for row in final_rows})!=len(final_rows) or any(observed.get(row['id'])!=row['condition'] or row['dealer_id']!='20866489' for row in final_rows):
            raise SourceFormatError('Partition root contains unverified IDs/categories')
        final_evidence={'kind':'final_root','section':'all','bounds':{},'parameters':parameters,'total':expected,'total_pages':pagination['total_page_count'],'page':1,'checked_at':datetime.now(timezone.utc).isoformat(),'ids':[row['id'] for row in final_rows],'sections':{row['id']:row['condition'] for row in final_rows}}
        return {'verified':True,'method':'disjoint_discounted_price_partitions','atomic_snapshot':False,'new_count_basis':'ALL IDs minus independently partitioned USED IDs','counts':{'newOffersCount':len(observed)-used['total'],'usedOffersCount':used['total']},'evidence':all_rows['evidence']+used['evidence']+[final_evidence]}
