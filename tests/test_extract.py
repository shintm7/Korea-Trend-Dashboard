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


    def test_double_escaped_html_entities_do_not_become_keywords(self):
        # 피드가 &hellip; 를 한 번 더 이스케이프(&amp;hellip;)해 보내면 feedparser 를 거쳐도 &hellip; 로 남는다
        for title in ("롯데 이동욱 신임 감독&hellip; 부산 현장", "롯데 이동욱 신임 감독&amp;hellip; 부산 현장",
                      "&quot;따옴표&quot; 테스트 기사 &foo; 끝", "&amp;quot;따옴표&amp;quot; 테스트 기사"):
            kws = extract(title, RULES).keywords
            for junk in ("hellip", "quot", "amp", "foo", "gt", "lt"):
                self.assertNotIn(junk, kws, title)
        self.assertIn("이동욱 신임 감독", extract("롯데 이동욱 신임 감독&hellip; 부산 현장", RULES).keywords)

    def test_entity_cleanup_does_not_change_dedupe_hash(self):
        # 해시가 바뀌면 이미 저장된 기사가 한 번 더 새 기사로 세어진다
        title = "롯데 이동욱 신임 감독&hellip; 부산 현장"
        self.assertEqual(extract(title, RULES).hash, title_hash(normalize_title(title)))

    def test_trailing_ellipsis_breaks_the_phrase(self):
        # 실제 헤드라인에서 흔한 형태: 말줄임표 뒤에 공백. (NFKC 가 … 를 ... 로 바꿔 예전에는 구절이 끊기지 않았다)
        for title in ("편의점 디저트 열풍… 삼성전자 양산", "편의점 디저트 열풍&hellip; 삼성전자 양산", "편의점 디저트 열풍... 삼성전자 양산"):
            kws = extract(title, RULES).keywords
            self.assertNotIn("열풍 삼성전자", kws, title)
            self.assertNotIn("디저트 열풍 삼성전자", kws, title)
            self.assertIn("편의점 디저트 열풍", kws, title)
            self.assertIn("삼성전자 양산", kws, title)

    def test_media_label_tokens_removed(self):
        kws = extract("뉴시스Pic 핫피플 [Oh! 이슈] 포토타임 배우 열애", RULES).keywords
        for junk in ("뉴시스", "Pic", "뉴시스Pic", "Oh", "핫피플", "포토타임", "타임", "피플"):
            self.assertNotIn(junk, kws)
        self.assertIn("배우 열애", kws)


if __name__ == "__main__":
    unittest.main()
