import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import rss  # noqa: E402
from radar.feeds import parse_feed_bytes  # noqa: E402

NOW = time.time()


class ParseTests(unittest.TestCase):
    def items(self, n=5):
        return [(f"테스트 기사 제목 {i}", NOW - i * 600) for i in range(n)]

    def test_normal(self):
        r = parse_feed_bytes("f", rss(self.items()), NOW, 24)
        self.assertEqual(r.status, "ok")
        self.assertEqual(len(r.items), 5)
        self.assertFalse(r.stale)

    def test_truncated_xml_still_reads_items(self):
        full = rss(self.items(8))
        r = parse_feed_bytes("f", full[: int(len(full) * 0.6)], NOW, 24)
        self.assertEqual(r.status, "ok")
        self.assertGreaterEqual(len(r.items), 2)

    def test_dc_date_only(self):
        r = parse_feed_bytes("f", rss(self.items(), dc_date=True), NOW, 24)
        self.assertEqual(r.status, "ok")
        self.assertTrue(all(i.ts for i in r.items))

    def test_euc_kr(self):
        r = parse_feed_bytes("f", rss([("한글 제목 확인", NOW)], encoding="euc-kr"), NOW, 24)
        self.assertEqual(r.items[0].title, "한글 제목 확인")

    def test_html_block_page_is_parse_error(self):
        r = parse_feed_bytes("f", "<html><body>Access Denied</body></html>".encode(), NOW, 24)
        self.assertIn(r.status, ("parse_error", "empty"))
        self.assertFalse(r.ok)

    def test_stale_feed_flagged(self):
        r = parse_feed_bytes("f", rss([("옛 기사", NOW - 30 * 3600)]), NOW, 24)
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.stale)


if __name__ == "__main__":
    unittest.main()
