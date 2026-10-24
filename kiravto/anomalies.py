"""Derived warnings retain offers and identify their observed comparison basis."""

def mark_model_price_anomalies(records):
    groups={}
    for row in records.values():
        brand=row.get('brand_code') or row.get('brand')
        model=row.get('model_code') or row.get('model')
        price=row.get('price')
        if brand and model and type(price) is int and price>0:
            groups.setdefault((brand,model),[]).append(price)
    references={}
    for key,prices in groups.items():
        prices.sort();count=len(prices)
        references[key]=(prices[(count-1)//2]+prices[count//2],count)
    for row in records.values():
        key=(row.get('brand_code') or row.get('brand'),row.get('model_code') or row.get('model'))
        price=row.get('price');reference=references.get(key)
        if reference and type(price) is int and price>=0 and price*20<=reference[0]:
            row['anomalies']=[*row.get('anomalies',[]),'price_below_tenth_of_model_median']
            row['model_price_reference']={'median':reference[0]/2,'sample_size':reference[1],'basis':'observed_run_base_prices'}
