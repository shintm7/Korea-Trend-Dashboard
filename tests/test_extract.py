import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.extract import Rules, extract, normalize_title, title_hash  # noqa: E402
from radar.util import read_wordlist  # noqa: E402

RULES = Rules.from_lists(read_wordlist(ROOT / "config/stopwords.txt"), read_wordlist(ROOT / "config/politics_terms.txt"))


class ExtractTests(unittest.TestCase):
    def test_tags_do_not_change_hash(self):
        a = title_hash(normalize_title("[속보] 두바이 쫀득 쿠키 품절"))
        b = title_hash(normalize_title("두바이 쫀득 쿠키 품절 (종합)"))
        c = title_hash(normalize_title("두바이  쫀득 쿠키 품절!"))
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_noun_units_and_ngrams(self):
        kws = extract("두바이 쫀득 쿠키 열풍…편의점 신상 품절 대란", RULES).keywords
        for k in ("두바이", "쿠키", "두바이 쫀득 쿠키", "신상 품절 대란", "편의점"):
            self.assertIn(k, kws)
        # 쉼표·말줄임표를 건너는 조합은 만들지 않는다
        self.assertNotIn("열풍 편의점", kws)

    def test_stopwords_removed(self):
        kws = extract("기자 오늘 단독 삼성전자 HBM4 양산", RULES).keywords
        self.assertNotIn("기자", kws)
        self.assertNotIn("오늘", kws)
        self.assertIn("삼성전자", kws)
        self.assertIn("HBM4", kws)

    def test_political_filter(self):
        self.assertTrue(extract("국회 국정감사 여야 공방 격화", RULES).political)
        self.assertTrue(extract("더불어민주당 새 대표 선출", RULES).political)
        self.assertEqual(extract("국회 국정감사 여야 공방 격화", RULES).keywords, [])

    def test_non_political_not_filtered(self):
        self.assertFalse(extract("편의점 디저트 매출 급증", RULES).political)

    def test_empty_title(self):
        self.assertEqual(extract("", RULES).keywords, [])


if __name__ == "__main__":
    unittest.main()
