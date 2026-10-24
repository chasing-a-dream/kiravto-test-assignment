"""Original Auto.ru CDN URL contract and content evidence for the first photo."""
import hashlib
import json
import io
import time
from PIL import Image,UnidentifiedImageError
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from kiravto.source import SourceFormatError

def original_url(url):
    try:
        parts=urlsplit(url)
        port=parts.port
    except ValueError: raise SourceFormatError('Malformed original photo location') from None
    prefix='/photo/get-autoru-vos/' if parts.hostname=='photo.auto.ru' else '/get-autoru-vos/'
    if parts.scheme!='https' or parts.hostname not in ('avatars.avto.ru','avatars.mds.yandex.net','photo.auto.ru') or port not in (None,443) or parts.username or parts.password or not parts.path.startswith(prefix) or parts.query or parts.fragment:
        raise SourceFormatError('Unsupported original photo location')
    segments=parts.path.strip('/').split('/')
    if len(segments)!=(5 if parts.hostname=='photo.auto.ru' else 4) or not all(segments): raise SourceFormatError('Source photo path changed')
    return urlunsplit(('https',parts.netloc,parts.path.rsplit('/',1)[0]+'/orig','',''))

class PhotoEvidence:
    def __init__(self,path,fetch,clock=time.time,max_age=86400):
        self.path=Path(path)
        self.fetch=fetch
        self.cache=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.fetched=0
        self.clock=clock
        self.max_age=max_age

    def enrich(self,offer):
        row=dict(offer)
        urls=[original_url(url) for url in row.get('photos',[])]
        row['photos']=urls
        if not urls:
            row['photos_original_verified']=False
            return row
        first=urls[0]
        evidence=self.cache.get(first)
        if evidence is None or not evidence.get('width') or not 0<=self.clock()-evidence.get('checked_at',0)<self.max_age:
            content,content_type=self.fetch(first)
            if not content or not (content.startswith(b'\xff\xd8\xff') or content.startswith(b'\x89PNG\r\n\x1a\n') or content.startswith(b'RIFF') and content[8:12]==b'WEBP'):
                raise SourceFormatError('Original photo content is not JPEG, PNG or WebP')
            try:
                with Image.open(io.BytesIO(content)) as picture:
                    # Phone JPEGs may carry MPF metadata and be decoded as MPO.
                    if picture.format not in ('JPEG','MPO','PNG','WEBP'): raise ValueError('Unsupported image')
                    width,height=picture.size
                    if width<2 or height<2 or width*height>50_000_000: raise ValueError('Unsupported image dimensions')
                    picture.verify()
                with Image.open(io.BytesIO(content)) as picture: picture.load()
            except (UnidentifiedImageError,OSError,ValueError,SyntaxError,OverflowError,Image.DecompressionBombError):
                raise SourceFormatError('Original image failed decoding or dimensions verification') from None
            evidence={'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content),'content_type':content_type,'width':width,'height':height,'checked_at':self.clock()}
            self.cache[first]=evidence
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.cache),encoding='utf-8')
            temporary.replace(self.path)
            self.fetched+=1
        row['photo_sha256']=evidence['sha256']
        row['photo_evidence']=evidence
        row['photos_original_verified']=True
        row['photo_verification_scope']='cdn_orig_url_contract_and_first_file'
        row['original_photo_candidates']=[]
        row['missing_fields']=[key for key in row.get('missing_fields',[]) if key!='verified_original_photos']
        return row
