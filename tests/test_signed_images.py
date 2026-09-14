"""GET signatures must survive the image service's metadata validation."""
import io
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ironic.common.image_service import HttpImageService


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.send_response(403 if 'X-Amz-Signature' in self.path else 200)
        self.send_header('Content-Length', '4')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', '4')
        self.end_headers()
        try:
            self.wfile.write(b'test')
        except (BrokenPipeError, ConnectionResetError):
            pass


class SignedImagesTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:%s/image.raw' % self.server.server_port

    def test_signed_get_validates_and_retains_full_size(self):
        service = HttpImageService()
        url = self.base + '?X-Amz-Signature=example&X-Amz-Algorithm=AWS4-HMAC-SHA256'
        response = service.validate_href(url)
        self.assertTrue(response.raw.closed)
        self.assertEqual(4, service.show(url)['size'])
        output = io.BytesIO()
        service.download(url, output)
        self.assertEqual(b'test', output.getvalue())

    def test_unsigned_url_keeps_head_validation(self):
        self.assertEqual(4, HttpImageService().show(self.base)['size'])
