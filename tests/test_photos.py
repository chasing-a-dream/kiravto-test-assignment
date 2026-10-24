import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from kiravto.photos import PhotoEvidence,original_url
from kiravto.source import SourceFormatError

URL='https://avatars.avto.ru/get-autoru-vos/17981265/b3b4126b4b4397d484084213b02f0848/1200x900'

def image_bytes(color='red'):
    output=io.BytesIO()
    Image.new('RGB',(8,6),color).save(output,format='JPEG')
    return output.getvalue()

class PhotoTests(unittest.TestCase):
    def test_phone_jpeg_with_mpf_metadata_is_supported(self):
        content=io.BytesIO()
        Image.new('RGB',(8,6),'red').save(content,format='MPO',save_all=True,append_images=[Image.new('RGB',(8,6),'blue')])
        with tempfile.TemporaryDirectory(dir='data') as folder:
            row=PhotoEvidence(Path(folder)/'images.json',lambda url:(content.getvalue(),'image/jpeg')).enrich({'photos':[URL]})
            self.assertEqual(row['photo_evidence']['width'],8)
            self.assertTrue(row['photos_original_verified'])

    def test_original_order_content_hash_and_restart_cache(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            calls=[]
            content=image_bytes()
            def fetch(url):
                calls.append(url)
                return content,'image/jpeg'
            path=Path(folder)/'image-evidence.json'
            second=URL.replace('b3b4126b4b4397d484084213b02f0848','second-photo')
            row=PhotoEvidence(path,fetch).enrich({'photos':[URL,second],'missing_fields':['verified_original_photos','trim']})
            self.assertEqual(row['photos'],[original_url(URL),original_url(second)])
            self.assertEqual(row['photo_sha256'],hashlib.sha256(content).hexdigest())
            self.assertEqual(row['missing_fields'],['trim'])
            self.assertEqual(row['photo_verification_scope'],'cdn_orig_url_contract_and_first_file')
            PhotoEvidence(path,fetch).enrich(row)
            self.assertEqual(calls,[original_url(URL)])

    def test_same_url_is_rechecked_after_expiry_and_corrupt_image_rejected(self):
        with tempfile.TemporaryDirectory(dir='data') as folder:
            clock=[1000]
            content=[image_bytes()]
            reader=PhotoEvidence(Path(folder)/'images.json',lambda url:(content[0],'image/jpeg'),clock=lambda:clock[0],max_age=10)
            first=reader.enrich({'photos':[URL]})
            clock[0]+=11
            content[0]=image_bytes('blue')
            second=reader.enrich(first)
            self.assertNotEqual(first['photo_sha256'],second['photo_sha256'])
            self.assertEqual(second['photo_evidence']['width'],8)
            clock[0]+=11
            content[0]=b'\xff\xd8\xff'
            with self.assertRaises(SourceFormatError): reader.enrich(second)

    def test_untrusted_host_or_html_response_is_rejected(self):
        with self.assertRaises(SourceFormatError): original_url('https://127.0.0.1/get-autoru-vos/a/b/1200x900')
        with self.assertRaises(SourceFormatError): original_url('https://avatars.avto.ru:bad/get-autoru-vos/a/b/orig')
        self.assertEqual(original_url('https://photo.auto.ru/photo/get-autoru-vos/a/b/1200x900'),'https://photo.auto.ru/photo/get-autoru-vos/a/b/orig')
        with self.assertRaises(SourceFormatError): original_url('https://avatars.avto.ru/get-autoru-vos/a/b/1200x900?token=not-permitted')
        with tempfile.TemporaryDirectory(dir='data') as folder:
            reader=PhotoEvidence(Path(folder)/'images.json',lambda url:(b'<html>try again</html>','text/html'))
            with self.assertRaises(SourceFormatError): reader.enrich({'photos':[URL]})
            self.assertFalse(reader.cache)
