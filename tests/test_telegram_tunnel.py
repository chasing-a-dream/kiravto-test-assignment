import unittest
from tools.telegram_tunnel import permitted

class TunnelTests(unittest.TestCase):
    def test_only_telegram_tls_destination_is_permitted(self):
        self.assertTrue(permitted('CONNECT api.telegram.org:443 HTTP/1.1'))
        for line in ('GET https://api.telegram.org/ HTTP/1.1','CONNECT 127.0.0.1:443 HTTP/1.1','CONNECT example.com:443 HTTP/1.1','CONNECT api.telegram.org:80 HTTP/1.1','CONNECT api.telegram.org.evil.example:443 HTTP/1.1','CONNECT api.telegram.org:443@127.0.0.1 HTTP/1.1'):
            with self.subTest(line=line):self.assertFalse(permitted(line))
