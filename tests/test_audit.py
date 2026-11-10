"""RSS 출처 감사 테스트: 중복·순위 로직(단위), scripts/audit_feeds.py 전체 실행(e2e), 제약(원문 미저장, 설정·데이터 불변,
수집 워크플로와의 독립성)."""
import csv
import datetime as dt
import hashlib
import http.server
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import rss  # noqa: E402
from radar import audit  # noqa: E402
from radar.audit import AItem, StoryIndex, greedy_rank, dedup_candidate_items  # noqa: E402
from radar.extract import Rules  # noqa: E402
from radar.feeds import Feed, FetchResult, Item, RobotsCache, parse_feed_bytes  # noqa: E402

NOW_ISO = "2026-11-05T12:00:00+09:00"
NOW = dt.datetime.fromisoformat(NOW_ISO).timestamp()
ND = {"min_shared_tokens": 3, "min_jaccard": 0.4, "common_token_share": 0.05}


def it(fid, pub, h, toks, political=False):
    return AItem(fid, pub, h, frozenset(toks), political)


class StoryIndexTests(unittest.TestCase):
    def test_exact_near_and_distinct(self):
        base = [it("a", "A", "h1", {"사과", "포도", "딸기", "수박"})]
        idx = StoryIndex(base, ND)
        self.assertEqual(idx.match(it("c", "C", "h1", {"x"})), "exact")
        self.assertEqual(idx.match(it("c", "C", "h9", {"사과", "포도", "딸기", "양파"})), "near")   # 4/5 겹침
        self.assertIsNone(idx.match(it("c", "C", "h9", {"사과", "포도", "고래", "상어"})))          # 2개만 겹침
        self.assertIsNone(idx.match(it("c", "C", "h9", {"사과", "고래", "상어", "독수리", "거북이"})))

    def test_political_items_are_not_indexed_and_skip_applies(self):
        base = [it("a", "A", "h1", {"사과", "포도", "딸기"}, political=True), it("b", "B", "h2", {"고래", "상어", "독수리"})]
        idx = StoryIndex(base, ND)
        self.assertIsNone(idx.match(it("c", "C", "h1", {"사과", "포도", "딸기"})))
        self.assertIsNone(idx.match(it("b", "B", "h2", {"고래", "상어", "독수리"}), skip=lambda o: o.pub == "B"))


class GreedyTests(unittest.TestCase):
    def test_overlapping_candidates_are_counted_once(self):
        base = [it("a", "A", "b1", {"사과", "포도", "딸기"})]
        story = lambda fid, h, w: it(fid, fid, h, w)
        c1 = [story("c1", f"x{i}", {f"가{i}", f"나{i}", f"다{i}"}) for i in range(5)]
        c2 = [story("c2", f"y{i}", {f"가{i}", f"나{i}", f"다{i}"}) for i in range(5)]            # c1 과 같은 사건
        c3 = [story("c3", f"z{i}", {f"라{i}", f"마{i}", f"바{i}"}) for i in range(3)]
        c4 = [story("c4", "b1", {"x"})]                                                         # 기존 피드와 같은 기사
        rank = dict(greedy_rank(base, {"c1": c1, "c2": c2, "c3": c3, "c4": c4}, ND))
        self.assertEqual(rank, {"c1": 5, "c3": 3, "c2": 0, "c4": 0})
        order = [f for f, _ in greedy_rank(base, {"c1": c1, "c2": c2, "c3": c3, "c4": c4}, ND)]
        self.assertEqual(order[:2], ["c1", "c3"])

    def test_deterministic(self):
        base = []
        cands = {f"c{i}": [it(f"c{i}", f"c{i}", f"h{i}{j}", {f"a{i}{j}", f"b{i}{j}", f"c{i}{j}"}) for j in range(2)] for i in range(4)}
        self.assertEqual(greedy_rank(base, cands, ND), greedy_rank(base, cands, ND))


class ClassifyTests(unittest.TestCase):
    cfg = audit.load_config()

    def m(self, **kw):
        base = dict(kind="candidate", feed=None, category="x", status="ok", items_window=20, newest_age_h=1.0,
                    hangul=0.9, political_share=0.1, dup_near=0.2, n_eval=18)
        base.update(kw)
        return audit.Measure(**base)

    def test_verdicts(self):
        c = lambda **kw: audit.classify(self.m(**kw), self.cfg)[0]
        self.assertEqual(c(), "통과")
        self.assertEqual(c(status="http_error", http=404), "제외")
        self.assertEqual(c(status="robots_blocked"), "제외")
        self.assertEqual(c(hangul=0.1), "제외")
        self.assertEqual(c(political_share=0.8), "제외")
        self.assertEqual(c(newest_age_h=100.0), "제외")
        self.assertEqual(c(newest_age_h=30.0), "보류")
        self.assertEqual(c(newest_age_h=None), "보류")
        self.assertEqual(c(items_window=3), "보류")
        self.assertEqual(c(dup_near=0.9), "제외")

    def test_hangul_ratio(self):
        self.assertEqual(audit.hangul_ratio(["한글 제목"]), 1.0)
        self.assertLess(audit.hangul_ratio(["Korea economy news"]), 0.1)


class InternalDeduplicationTests(unittest.TestCase):
    def test_candidate_internal_exact_duplicates_collapsed_to_one(self):
        base = []
        dup_items = [it("c1", "pub", "hash_same", {"사과", "포도", "딸기"}) for _ in range(5)]
        deduped = dedup_candidate_items(dup_items, ND)
        self.assertEqual(len(deduped), 1)
        rank = dict(greedy_rank(base, {"c1": dup_items}, ND))
        self.assertEqual(rank["c1"], 1)

    def test_candidate_internal_near_duplicates_collapsed_to_one(self):
        base = []
        near_items = [
            it("c1", "pub", "h1", {"사과", "포도", "딸기", "수박"}),
            it("c1", "pub", "h2", {"사과", "포도", "딸기", "참외"}),
            it("c1", "pub", "h3", {"사과", "포도", "딸기", "복숭아"}),
            it("c1", "pub", "h4", {"사과", "포도", "딸기", "자두"}),
            it("c1", "pub", "h5", {"사과", "포도", "딸기", "바나나"}),
        ]
        deduped = dedup_candidate_items(near_items, ND)
        self.assertEqual(len(deduped), 1)
        rank = dict(greedy_rank(base, {"c1": near_items}, ND))
        self.assertEqual(rank["c1"], 1)

    def test_internal_and_cross_candidate_deduplication_combined(self):
        base = [it("base", "base", "b1", {"우주", "로켓", "발사"})]
        c1 = [
            it("c1", "c1", "hA1", {"사과", "포도", "딸기"}),
            it("c1", "c1", "hA2", {"사과", "포도", "딸기"}),
            it("c1", "c1", "hA3", {"사과", "포도", "딸기"}),
            it("c1", "c1", "hB", {"고양이", "강아지", "호랑이"}),
        ]
        c2 = [
            it("c2", "c2", "hA4", {"사과", "포도", "딸기"}),
            it("c2", "c2", "hC", {"독수리", "갈매기", "비둘기"}),
        ]
        rank = dict(greedy_rank(base, {"c1": c1, "c2": c2}, ND))
        self.assertEqual(rank["c1"], 2)  # A and B (internal dup of A collapsed)
        self.assertEqual(rank["c2"], 1)  # C only (A covered by c1)


class FutureItemAuditTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.from_lists([], [], 3, 80)
        self.cfg = audit.load_config()
        self.feed = Feed(id="f1", pub="pub", name="pub", section="x", title="t", url="http://x/rss")

    def test_future_items_excluded_from_window_and_fresh(self):
        items = [
            Item("과거 기사 1", "http://x/1", NOW - 3600),
            Item("과거 기사 2", "http://x/2", NOW - 7200),
            Item("미래 기사 1", "http://x/3", NOW + 3600),
            Item("미래 기사 2", "http://x/4", NOW + 7200),
        ]
        res = FetchResult("f1", "ok", items=items, newest_ts=NOW + 7200)
        valid, st, newest_valid_ts = audit.build_items(self.feed, res, self.rules, NOW, 48.0, 24.0)
        self.assertEqual(len(valid), 2)
        self.assertEqual(st["future"], 2)
        self.assertEqual(st["window"], 2)
        self.assertEqual(st["fresh"], 2)
        self.assertEqual(newest_valid_ts, NOW - 3600)

    def test_single_future_item_does_not_distort_newest_age(self):
        old_ts = NOW - 100 * 3600
        items = [Item(f"오래된 기사 {i}", f"http://x/{i}", old_ts) for i in range(5)]
        items.append(Item("미래 기사", "http://x/f", NOW + 3600))
        res = FetchResult("f1", "ok", items=items, newest_ts=NOW + 3600)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        self.assertEqual(m.newest_age_h, 100.0)
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "제외")
        self.assertIn("정지", r)

    def test_future_ratio_threshold_holds_candidate_feed(self):
        items = [Item(f"기사 {i}", f"http://x/{i}", NOW - 3600) for i in range(7)]
        items += [Item(f"미래 {i}", f"http://x/f{i}", NOW + 3600) for i in range(3)]
        res = FetchResult("f1", "ok", items=items, newest_ts=NOW + 3600)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("시간대 의심", r)

    def test_future_ratio_below_threshold_does_not_hold_feed(self):
        items = [Item(f"기사 {i}", f"http://x/{i}", NOW - 3600) for i in range(9)]
        items.append(Item("미래 1", "http://x/f1", NOW + 3600))
        res = FetchResult("f1", "ok", items=items, newest_ts=NOW + 3600)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "통과")

    def test_naive_kst_auto_correction_passes(self):
        xml = rss([(f"기사 {i}", NOW + 9 * 3600 - 600 * i) for i in range(10)])
        res = parse_feed_bytes("f1", xml, NOW, 48.0)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        self.assertEqual(m.future, 0)
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "통과")

    def test_extreme_future_beyond_kst_held_as_suspicious_tz(self):
        xml = rss([(f"미래기사 {i}", NOW + 12 * 3600 - 600 * i) for i in range(5)])
        res = parse_feed_bytes("f1", xml, NOW, 48.0)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        self.assertGreaterEqual(m.future, 3)
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("시간대 의심", r)


class UndatedItemAuditTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules.from_lists([], [], 3, 80)
        self.cfg = audit.load_config()
        self.feed = Feed(id="f1", pub="pub", name="pub", section="x", title="t", url="http://x/rss")

    def test_undated_items_excluded_from_items_and_window(self):
        items = [
            Item("날짜 없는 기사 1", "http://x/1", None),
            Item("날짜 없는 기사 2", "http://x/2", None),
            Item("유효 기사 1", "http://x/3", NOW - 3600),
        ]
        res = FetchResult("f1", "ok", items=items, newest_ts=NOW - 3600)
        valid, st, newest_valid_ts = audit.build_items(self.feed, res, self.rules, NOW, 48.0, 24.0)
        self.assertEqual(len(valid), 1)
        self.assertEqual(st["undated"], 2)
        self.assertEqual(st["window"], 1)

    def test_candidate_all_undated_held_for_insufficient_dates(self):
        items = [Item(f"기사 {i}", f"http://x/{i}", None) for i in range(10)]
        res = FetchResult("f1", "ok", items=items, newest_ts=None)
        m, valid = audit.measure_feed("candidate", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        v, r = audit.classify(m, self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("날짜", r)

    def test_current_feed_all_undated_marked_unjudged(self):
        items = [Item(f"기사 {i}", f"http://x/{i}", None) for i in range(10)]
        res = FetchResult("f1", "ok", items=items, newest_ts=None)
        m, valid = audit.measure_feed("current", self.feed, res, 100, self.rules, NOW, self.cfg, 48.0, "x")
        if m.status in ("ok", "not_modified"):
            if m.newest_age_h is None and m.undated > 0 and m.items_window == 0:
                m.verdict = "판정 불가"
        self.assertEqual(m.verdict, "판정 불가")


class RobotsHandlingTests(unittest.TestCase):
    def test_robots_explicit_disallow_blocked(self):
        class MockResp:
            status_code = 200
            text = "User-agent: *\nDisallow: /\n"
        class MockSession:
            def get(self, url, **kw):
                return MockResp()
        cache = RobotsCache(MockSession(), "RadarBot", 10.0)
        self.assertEqual(cache.check("http://example.com/rss"), "blocked")
        cfg = audit.load_config()
        m = audit.Measure(kind="candidate", feed=None, category="x", status="robots_blocked")
        v, r = audit.classify(m, cfg)
        self.assertEqual(v, "제외")
        self.assertIn("robots.txt 불허", r)

    def test_robots_403_treated_as_blocked(self):
        class MockResp:
            status_code = 403
            text = "Forbidden"
        class MockSession:
            def get(self, url, **kw):
                return MockResp()
        cache = RobotsCache(MockSession(), "RadarBot", 10.0)
        self.assertEqual(cache.check("http://example.com/rss"), "blocked")

    def test_robots_500_unavailable_and_held(self):
        class MockResp:
            status_code = 500
            text = "Server Error"
        class MockSession:
            def get(self, url, **kw):
                return MockResp()
        cache = RobotsCache(MockSession(), "RadarBot", 10.0)
        self.assertEqual(cache.check("http://example.com/rss"), "unavailable")
        cfg = audit.load_config()
        m = audit.Measure(kind="candidate", feed=None, category="x", status="robots_unavailable")
        v, r = audit.classify(m, cfg)
        self.assertEqual(v, "보류")
        self.assertIn("확인 불가", r)

    def test_robots_retry_recovers_on_second_attempt(self):
        calls = []
        class MockSession:
            def get(self, url, **kw):
                calls.append(url)
                if len(calls) == 1:
                    resp = MockResp()
                    resp.status_code = 500
                    return resp
                resp = MockResp()
                resp.status_code = 200
                resp.text = "User-agent: *\nAllow: /\n"
                return resp
        class MockResp:
            status_code = 200
            text = ""
        cache = RobotsCache(MockSession(), "RadarBot", 10.0)
        res = cache.check("http://example.com/rss")
        self.assertEqual(res, "allowed")
        self.assertEqual(len(calls), 2)

    def test_robots_transient_failure_not_permanently_cached(self):
        call_count = [0]
        class MockResp:
            def __init__(self, code, text):
                self.status_code = code
                self.text = text
        class MockSession:
            def get(self, url, **kw):
                call_count[0] += 1
                if call_count[0] <= 2:
                    return MockResp(500, "Error")
                return MockResp(200, "User-agent: *\nAllow: /\n")
        cache = RobotsCache(MockSession(), "RadarBot", 10.0)
        self.assertEqual(cache.check("http://example.com/rss"), "unavailable")
        self.assertEqual(cache.check("http://example.com/rss"), "allowed")

    def test_robots_distinct_hosts_parallel_locking(self):
        cache = RobotsCache(None, "RadarBot", 10.0)
        lock1 = cache._get_host_lock("http://host1")
        lock2 = cache._get_host_lock("http://host2")
        self.assertIsNot(lock1, lock2)
        lock1_again = cache._get_host_lock("http://host1")
        self.assertIs(lock1, lock1_again)


class GranularStatusClassificationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = audit.load_config()

    def m(self, **kw):
        base = dict(kind="candidate", feed=None, category="x", status="ok", items_window=20, newest_age_h=1.0,
                    hangul=0.9, political_share=0.1, dup_near=0.2, n_eval=18)
        base.update(kw)
        return audit.Measure(**base)

    def test_empty_feed(self):
        v, r = audit.classify(self.m(status="empty"), self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("빈 피드", r)

    def test_parse_error(self):
        v, r = audit.classify(self.m(status="parse_error", error="XML SyntaxError"), self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("파싱 실패", r)

    def test_timeout_and_5xx_held(self):
        v, r = audit.classify(self.m(status="timeout"), self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("일시 장애", r)

        v, r = audit.classify(self.m(status="http_error", http=503), self.cfg)
        self.assertEqual(v, "보류")
        self.assertIn("일시 장애", r)

    def test_client_error_404_excluded(self):
        v, r = audit.classify(self.m(status="http_error", http=404), self.cfg)
        self.assertEqual(v, "제외")
        self.assertIn("접근 실패", r)

    def test_non_korean_excluded(self):
        v, r = audit.classify(self.m(hangul=0.2), self.cfg)
        self.assertEqual(v, "제외")
        self.assertIn("비한국어", r)


# ---- e2e: scripts/audit_feeds.py 를 별도 프로세스로 실행 (fixture, 네트워크 없음) ----
WORDS = ("사과 포도 딸기 수박 고구마 감자 양파 마늘 고추 배추 오이 호박 버섯 두부 김치 라면 우동 냉면 떡볶이 치킨 피자 햄버거 커피 녹차 맥주 "
         "와인 막걸리 소주 고양이 강아지 코끼리 호랑이 독수리 고래 상어 거북이 나비 매미 자전거 오토바이 기차 비행기 잠수함 헬리콥터 냉장고 "
         "세탁기 청소기 에어컨 선풍기 텔레비전 노트북 스마트폰 카메라 시계 안경 우산 가방 신발 "
         "도서관 박물관 미술관 영화관 야구장 축구장 수영장 놀이공원 설악산 낙동강 독도 울릉도 경주 전주 안동 속초 제주도 바이올린 피아노 기타 트럼펫 장구").split()


def story(n, suffix="출시"):
    """n 번째 고유 사건 제목: 서로 겹치지 않는 단어 3개."""
    a, b, c = WORDS[3 * n], WORDS[3 * n + 1], WORDS[3 * n + 2]
    return f"{a} {b} {c} {suffix}"


def mins(items, base=NOW):
    return [(t, base - 600 * (i + 1)) for i, t in enumerate(items)]


class E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        t = cls.tmp
        fx = t / "fx"
        fx.mkdir()
        # 현재 피드: A(스토리 0~3), B(스토리 4~6 + A 와 같은 기사 1 + 같은 사건 다른 문구 1)
        a = [story(i) for i in range(4)]
        near = f"{WORDS[0]} {WORDS[1]} {WORDS[2]} 소식 전해"          # 스토리 0 과 단어 3개 공유, 문구는 다름
        b = [story(i) for i in range(4, 7)] + [a[1], near]
        (fx / "cur-a.xml").write_bytes(rss(mins(a)))
        (fx / "cur-b.xml").write_bytes(rss(mins(b)))
        (fx / "cur-c.xml").write_bytes(rss(mins([story(7)])))
        # 후보
        cands = []

        def add(name, group, items=None, raw=None, flag=None):
            n = len(cands) + 1
            cands.append([name, group, f"https://{name}.example/rss", "전체", "50", "신규"])
            fid = f"cand{n:03d}"
            if flag:
                (fx / f"{fid}.{flag}").write_text("x")
            elif raw is not None:
                (fx / f"{fid}.xml").write_bytes(raw)
            elif items is not None:
                (fx / f"{fid}.xml").write_bytes(rss(items))
            return fid

        new1 = [story(i) for i in range(8, 13)]
        cls.fid = {
            "dup": add("중복매체", "IT·과학·전문", mins(a + [near])),                 # 전부 기존과 중복
            "new1": add("신규1", "자동차", mins(new1)),
            "new2": add("신규2", "자동차", mins(new1)),                                # 신규1 과 같은 기사 (순위에서 0)
            "new3": add("신규3", "스포츠·연예", mins([story(i) for i in range(13, 17)])),
            "fail": add("실패매체", "지역지", None),                                   # fixture 없음 -> 404
            "stale": add("정지매체", "지역지", [(t, NOW - 5 * 86400) for t in [story(17)]]),
            "eng": add("영문매체", "영자지", mins(["Korea economy grows fast", "Seoul markets rally again", "New phone launch event",
                                              "Weather turns cold today", "Local team wins final"])),
            "pol": add("정치매체", "인터넷·시사", mins([f"대통령 {story(i)}" for i in range(17, 22)])),
            "robots": add("막힌매체", "지역지", flag="robots_blocked"),
            "sparse": add("드문매체", "지역지", mins([story(22)])),
        }
        cls.titles = set(a + b + new1 + [story(i) for i in range(13, 17)] + [story(i) for i in range(17, 23)] + [near])
        feeds = {"feeds": [
            {"id": "cur-a", "pub": "A", "name": "A", "section": "economy", "title": "t", "url": "https://a.example/rss"},
            {"id": "cur-b", "pub": "B", "name": "B", "section": "society", "title": "t", "url": "https://b.example/rss"},
            {"id": "cur-c", "pub": "C", "name": "C", "section": "sports", "title": "t", "url": "https://c.example/rss"},
        ]}
        (t / "feeds.yaml").write_text(yaml.safe_dump(feeds, allow_unicode=True), encoding="utf-8")
        with open(t / "cands.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["publisher", "group", "url", "scope", "items_reported", "status_vs_current"])
            w.writerows(cands)
        acfg = audit.load_config()
        acfg["thresholds"].update(min_items_window=3, min_marginal_gain=3)
        (t / "audit.yaml").write_text(yaml.safe_dump(acfg, allow_unicode=True), encoding="utf-8")
        settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text(encoding="utf-8"))
        settings["host_min_interval_sec"] = 0
        (t / "settings.yaml").write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")

        # 실제 설정·데이터 파일의 해시 (감사 전후 비교)
        cls.guarded = [ROOT / "config/feeds.yaml", ROOT / "config/settings.yaml", ROOT / "config/feeds_candidates.csv",
                       ROOT / "config/politics_terms.txt", ROOT / "config/stopwords.txt"]
        cls.before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in cls.guarded}
        data = ROOT / "data"
        cls.data_before = sorted(str(p.relative_to(ROOT)) + hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in data.rglob("*") if p.is_file()) if data.exists() else []
        cls.out = t / "out"
        cls.proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/audit_feeds.py"), "--settings", str(t / "settings.yaml"),
             "--feeds", str(t / "feeds.yaml"), "--candidates", str(t / "cands.csv"), "--audit-config", str(t / "audit.yaml"),
             "--fixtures", str(fx), "--now", NOW_ISO, "--out-dir", str(cls.out)],
            capture_output=True, text=True, timeout=300)
        rows = []
        if (cls.out / "audit_feeds.csv").exists():
            with open(cls.out / "audit_feeds.csv", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        cls.rows = {r["id"]: r for r in rows}

    def test_runs_and_writes_outputs(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-800:])
        for n in ("audit_feeds.csv", "audit_distribution.csv", "audit_report.md"):
            self.assertTrue((self.out / n).exists(), n)

    def test_current_feed_status(self):
        for fid in ("cur-a", "cur-b", "cur-c"):
            self.assertEqual(self.rows[fid]["verdict"], "정상")
        # B 의 기사 중 A 와 같은 기사 1건, 같은 사건 1건
        b = self.rows["cur-b"]
        self.assertEqual(float(b["dup_exact"]), round(1 / 5, 3))
        self.assertEqual(float(b["dup_near"]), round(2 / 5, 3))

    def test_candidate_verdicts(self):
        v = lambda k: self.rows[self.fid[k]]["verdict"]
        r = lambda k: self.rows[self.fid[k]]["reason"]
        self.assertEqual(v("dup"), "제외")
        self.assertIn("중복", r("dup"))
        self.assertEqual(v("new1"), "추가 검토")
        self.assertEqual(v("new3"), "추가 검토")
        self.assertEqual(v("new2"), "보류")            # 신규1 이 이미 가져온 기사
        self.assertIn("신규 기여 낮음", r("new2"))
        self.assertEqual(v("fail"), "제외")
        self.assertIn("접근 실패", r("fail"))
        self.assertEqual(v("stale"), "제외")
        self.assertIn("정지", r("stale"))
        self.assertEqual(v("eng"), "제외")
        self.assertIn("비한국어", r("eng"))
        self.assertEqual(v("pol"), "제외")
        self.assertIn("정치", r("pol"))
        self.assertEqual(v("robots"), "제외")
        self.assertIn("robots", r("robots"))
        self.assertEqual(v("sparse"), "보류")

    def test_rank_and_gain(self):
        new1, new3 = self.rows[self.fid["new1"]], self.rows[self.fid["new3"]]
        self.assertEqual(new1["rank"], "1")
        self.assertEqual(new1["marginal_gain"], "5")
        self.assertEqual(new3["rank"], "2")
        self.assertEqual(new3["marginal_gain"], "4")
        self.assertEqual(float(new1["new_rate"]), 1.0)

    def test_report_sections(self):
        md = (self.out / "audit_report.md").read_text(encoding="utf-8")
        for s in ("## 1. 요약", "## 2. 현재 피드 상태", "## 4. 매체별·분야별 분포", "## 5. 신규 기사 제공 비율",
                  "## 6. 추가 가치 순위", "## 7. 최종 분류안", "### 추가 검토 (2개)", "### 제외"):
            self.assertIn(s, md)

    def test_distribution_counts_only_new_stories_of_added_feeds(self):
        with open(self.out / "audit_distribution.csv", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        cat = {r["name"]: r for r in rows if r["axis"] == "category"}
        self.assertEqual(cat["자동차"]["items_before"], "0")
        self.assertEqual(cat["자동차"]["items_after_added_new_only"], "5")
        self.assertEqual(cat["스포츠·연예"]["items_after_added_new_only"], "4")

    def test_no_headline_text_in_any_output(self):
        blob = ""
        for p in self.out.iterdir():
            blob += p.read_text(encoding="utf-8-sig")
        blob += self.proc.stdout + self.proc.stderr
        leaked = [t for t in self.titles if t in blob]
        self.assertEqual(leaked, [])
        # 제목을 이루는 단어 3개 이상이 한 줄에 나란히 나오는 것도 없어야 한다
        for line in blob.splitlines():
            self.assertFalse(sum(w in line for w in ("사과", "포도", "딸기", "수박", "고구마")) >= 3, line[:80])

    def test_config_and_data_unchanged(self):
        after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.guarded}
        self.assertEqual(after, self.before)
        data = ROOT / "data"
        data_after = sorted(str(p.relative_to(ROOT)) + hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in data.rglob("*") if p.is_file()) if data.exists() else []
        self.assertEqual(data_after, self.data_before)


class LiveHttpPolicyTests(unittest.TestCase):
    """실제 HttpFetcher 경로(로컬 서버): robots.txt 존중, 같은 서버 최소 요청 간격, 응답 시간 기록."""

    def test_robots_interval_and_timing(self):
        stamps, paths = [], []
        body = rss(mins([story(i) for i in range(5)], base=time.time()))

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.path == "/robots.txt":
                    out = b"User-agent: *\nDisallow: /blocked\n"
                    ctype = "text/plain"
                else:
                    stamps.append(time.time())
                    out, ctype = body, "application/rss+xml"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()

        def stop():
            srv.shutdown()
            srv.server_close()
            th.join(timeout=5)

        self.addCleanup(stop)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        t = Path(tempfile.mkdtemp())
        (t / "feeds.yaml").write_text(yaml.safe_dump({"feeds": [
            {"id": "cur-a", "pub": "A", "name": "A", "section": "economy", "title": "t", "url": f"{base}/a.xml"}]}), encoding="utf-8")
        with open(t / "cands.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["publisher", "group", "url", "scope", "items_reported", "status_vs_current"])
            w.writerow(["후보1", "자동차", f"{base}/c1.xml", "전체", "5", "신규"])
            w.writerow(["막힘", "자동차", f"{base}/blocked.xml", "전체", "5", "신규"])
            w.writerow(["후보3", "자동차", f"{base}/c3.xml", "전체", "5", "신규"])
        out = t / "out"
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/audit_feeds.py"), "--feeds", str(t / "feeds.yaml"),
                               "--candidates", str(t / "cands.csv"), "--out-dir", str(out)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        with open(out / "audit_feeds.csv", encoding="utf-8-sig", newline="") as f:
            rows = {r["publisher"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["막힘"]["status"], "robots_blocked")
        self.assertNotIn("/blocked.xml", paths)                    # 막힌 주소는 요청조차 하지 않는다
        self.assertEqual(rows["후보1"]["status"], "ok")
        self.assertNotEqual(rows["후보1"]["response_ms"], "")      # 응답 시간 기록
        stamps.sort()
        self.assertEqual(len(stamps), 3)                           # a.xml, c1.xml, c3.xml
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(g >= 0.95 for g in gaps), gaps)        # 같은 서버에는 1초 이상 간격


class IndependenceTests(unittest.TestCase):
    def wf(self, name):
        return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))

    def test_audit_workflow_is_manual_readonly_and_independent(self):
        w = self.wf("audit.yml")
        on = w.get("on", w.get(True))
        self.assertEqual(list(on.keys()), ["workflow_dispatch"])           # schedule/push 없음
        self.assertEqual(w["permissions"], {"contents": "read"})            # 저장소 쓰기 없음
        self.assertEqual(w["concurrency"]["group"], "audit")               # collect 와 다른 그룹
        text = (ROOT / ".github/workflows/audit.yml").read_text(encoding="utf-8")
        for bad in ("git push", "git commit", "cache/save", "cache/restore", "state", "data/"):
            self.assertNotIn(bad, text)
        self.assertIn("upload-artifact", text)

    def test_collect_workflow_untouched_by_audit(self):
        w = self.wf("collect.yml")
        on = w.get("on", w.get(True))
        self.assertIn("schedule", on)
        self.assertEqual(w["concurrency"]["group"], "collect")
        text = (ROOT / ".github/workflows/collect.yml").read_text(encoding="utf-8")
        self.assertNotIn("audit", text)

    def test_collection_code_does_not_import_audit(self):
        import ast
        files = [p for p in (ROOT / "radar").glob("*.py") if p.name != "audit.py"] + \
                [ROOT / "scripts" / n for n in ("collect.py", "status.py", "simulate.py", "probe_feeds.py")]
        bad = []
        for p in files:
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    ([node.module or ""] + [a.name for a in node.names]) if isinstance(node, ast.ImportFrom) else []
                if any(n == "audit" or n.endswith(".audit") for n in names):
                    bad.append(p.name)
        self.assertEqual(bad, [])

    def test_audit_uses_collection_policy(self):
        src = (ROOT / "scripts/audit_feeds.py").read_text(encoding="utf-8")
        self.assertIn("HttpFetcher(settings)", src)           # robots.txt·서버당 간격·타임아웃·재시도는 HttpFetcher 가 처리
        s = yaml.safe_load((ROOT / "config/settings.yaml").read_text(encoding="utf-8"))
        self.assertGreaterEqual(s["host_min_interval_sec"], 1.0)

    def test_audit_workflow_shell_and_artifact_checks(self):
        text = (ROOT / ".github/workflows/audit.yml").read_text(encoding="utf-8")
        self.assertIn("shell: bash", text)
        self.assertIn("if-no-files-found: error", text)


if __name__ == "__main__":
    unittest.main()
