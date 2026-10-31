"""실제 HTTP 경로(robots.txt, 조건부 요청, 오류 코드)를 로컬 서버로 점검한다."""
import http.server
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import rss  # noqa: E402
from radar.feeds import Feed, HttpFetcher  # noqa: E402
from radar.util import load_settings  # noqa: E402

NOW = time.time()
BODY = rss([("편의점 디저트 신상 출시", NOW - 600), ("삼성전자 HBM4 양산", NOW - 1200)])
ETAG = '"v1"'


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /private\n", "text/plain")
        elif self.path == "/ok.xml":
            if self.headers.get("If-None-Match") == ETAG:
                self.send_response(304)
                self.end_headers()
            else:
                self._send(200, BODY, "application/rss+xml", {"ETag": ETAG})
        elif self.path == "/private/feed.xml":
            self._send(200, BODY, "application/rss+xml")
        elif self.path == "/block.xml":
            self._send(200, b"<html><body>Access Denied</body></html>", "text/html")
        elif self.path == "/boom.xml":
            self._send(503, b"busy", "text/plain")
        else:
            self._send(404, b"nope", "text/plain")

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cfg = {**load_settings(), "host_min_interval_sec": 0, "retries": 0, "timeout_sec": 5}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def feed(self, path):
        return Feed("t", "p", "P", "economy", "t", self.base + path)

    def test_ok_and_conditional_get(self):
        state = {}
        f = HttpFetcher(self.cfg, state)
        r = f.fetch(self.feed("/ok.xml"), now=NOW)
        self.assertEqual((r.status, len(r.items), r.etag), ("ok", 2, ETAG))
        state["t"] = {"etag": r.etag, "last_modified": None}
        r2 = f.fetch(self.feed("/ok.xml"), now=NOW)
        self.assertEqual(r2.status, "not_modified")

    def test_robots_blocked(self):
        r = HttpFetcher(self.cfg).fetch(self.feed("/private/feed.xml"), now=NOW)
        self.assertEqual(r.status, "robots_blocked")
        self.assertEqual(r.items, [])

    def test_http_errors(self):
        f = HttpFetcher(self.cfg)
        self.assertEqual(f.fetch(self.feed("/missing.xml"), now=NOW).status, "http_error")
        r = f.fetch(self.feed("/boom.xml"), now=NOW)
        self.assertEqual((r.status, r.http), ("http_error", 503))

    def test_block_page_is_parse_error(self):
        r = HttpFetcher(self.cfg).fetch(self.feed("/block.xml"), now=NOW)
        self.assertIn(r.status, ("parse_error", "empty"))

    def test_connection_refused_is_network_error(self):
        f = HttpFetcher(self.cfg)
        r = f.fetch(Feed("t", "p", "P", "e", "t", "http://127.0.0.1:1/x.xml"), now=NOW)
        self.assertIn(r.status, ("robots_unavailable", "network_error"))


if __name__ == "__main__":
    unittest.main()
