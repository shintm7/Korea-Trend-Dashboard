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


    def test_naive_kst_stamps_read_as_utc_are_shifted_back(self):
        # 한국 시각을 시간대 없이 적은 피드: 실제로는 방금~2시간 전인 기사가 UTC로 읽혀 7~9시간 미래로 보인다
        items = [(f"기사 {i}", NOW + 9 * 3600 - i * 1800) for i in range(6)]
        r = parse_feed_bytes("f", rss(items), NOW, 24)
        self.assertLessEqual(max(i.ts for i in r.items), NOW + 5)
        self.assertFalse(r.stale)

    def test_single_future_item_is_not_shifted(self):
        items = [("예약 기사", NOW + 3 * 3600)] + [(f"기사 {i}", NOW - i * 3600) for i in range(1, 8)]
        r = parse_feed_bytes("f", rss(items), NOW, 24)
        self.assertGreater(max(i.ts for i in r.items), NOW + 3600)


if __name__ == "__main__":
    unittest.main()
