"""Structured JSON-LD adapter. Missing required fields are explicit, never invented.

This adapter is deliberately incomplete: JSON-LD does not provide the whole
required contract. It must not mark a run complete until a richer source exists.
"""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

class SourceFormatError(ValueError):
    pass

class SourceTransportError(SourceFormatError):
    """A bounded ordinary transport retry failed; never a protection/schema error."""
    pass

class InventoryDriftError(SourceFormatError):
    """Validated inventory responses disagree; never an HTTP/schema refusal."""
    def __init__(self, message, **context):
        self.context = context
        super().__init__(message + ('; ' + json.dumps(context, ensure_ascii=False, sort_keys=True) if context else ''))

class JsonLD(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') == 'application/ld+json':
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.active = False
            try:
                self.documents.append(json.loads(''.join(self.parts)))
            except ValueError as exc:
                raise SourceFormatError('Invalid JSON-LD in source response') from exc

def documents(html):
    parser = JsonLD()
    parser.feed(html)
    if not parser.documents:
        raise SourceFormatError('No JSON-LD found; source blocked or format changed')
    return parser.documents

def offer_id(url):
    parts = urlsplit(url)
    if parts.hostname != 'auto.ru' or not re.fullmatch(r'\d+-[a-zA-Z0-9]+', parts.path.rstrip('/').split('/')[-1]):
        raise SourceFormatError('Unexpected offer URL')
    return parts.path.rstrip('/').split('/')[-1]

def parse_listing(data):
    if isinstance(data, list):
        data = next((x for x in data if x.get('@type') == 'AggregateOffer'), {})
    if data.get('@type') != 'AggregateOffer' or not isinstance(data.get('offerCount'), int) or not isinstance(data.get('offers'), list):
        raise SourceFormatError('AggregateOffer schema changed')
    rows = []
    for offer in data['offers']:
        if not isinstance(offer.get('price'), (int,float)) or not offer.get('url'):
            raise SourceFormatError('Offer price or URL missing')
        rows.append({'id': offer_id(offer['url']), 'url': offer['url'], 'price': offer['price'], 'name': offer.get('image', {}).get('name'), 'availability': offer.get('availability')})
    return rows, data['offerCount']

def parse_card(data):
    if isinstance(data, list):
        data = next((x for x in data if x.get('@type') == 'Product'), {})
    if data.get('@type') != 'Product' or not data.get('offers') or not data.get('productionDate'):
        raise SourceFormatError('Product schema changed')
    offer = data['offers']
    url = offer.get('url', '')
    photo_groups = {}
    for image in data.get('image', []):
        uri = image.get('contentUrl', '')
        base = uri.rsplit('/', 1)[0]
        area = int(image.get('width', 0)) * int(image.get('height', 0))
        if area > photo_groups.get(base, (0,''))[0]:
            photo_groups[base] = (area, uri)
    brand = {'ZEEKR':'Zeekr','MERCEDES':'Mercedes-Benz'}.get(data.get('brand'), data.get('brand'))
    return {'id': offer_id(url), 'url': url, 'name': data.get('name'), 'brand': brand, 'year': int(data['productionDate']), 'price': offer.get('price'), 'description': data.get('description'), 'color': data.get('color'), 'photos': [x[1] for x in photo_groups.values()], 'photo_quality': 'largest_declared_not_verified_original', 'missing_fields': ['model','generation','trim','mileage','discount_price','discount_conditions','engine','transmission','drive','body','vin_visibility','owners','pts','customs','posted_at'], 'source_complete': False}
