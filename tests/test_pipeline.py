import csv
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from unittest.mock import patch

from helpers import write_feed  # noqa: E402
from radar import alias, quality, store  # noqa: E402
from radar.extract import Rules  # noqa: E402
from radar.feeds import Feed, FixtureFetcher  # noqa: E402
from radar.pipeline import finalize_days, prune_finalized_days, run_once, write_day  # noqa: E402
from radar.state import load_state, new_state, save_state  # noqa: E402
from radar.util import KST, load_settings, read_wordlist  # noqa: E402

CFG = load_settings()
RULES = Rules.from_lists(read_wordlist(ROOT / "config/stopwords.txt"), read_wordlist(ROOT / "config/politics_terms.txt"))
FEEDS = [Feed("a-eco", "a", "A", "economy", "경제", "u1"), Feed("b-eco", "b", "B", "economy", "경제", "u2"),
         Feed("b-ent", "b", "B", "entertainment", "연예", "u3")]


def at(day, hhmm):
    h, m = hhmm.split(":")
    return dt.datetime.fromisoformat(f"{day}T{h}:{m}:00+09:00").timestamp()


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx, self.data = self.tmp / "fx", self.tmp / "data"
        self.fetcher = FixtureFetcher(self.fx, CFG)
        self.state = new_state()

    def run_at(self, day, hhmm, **kw):
        return run_once(CFG, FEEDS, self.fetcher, self.state, at(day, hhmm), RULES, log=lambda *_: None, **kw)

    def test_dedupe_across_runs_and_outlets(self):
        t = at("2026-11-02", "09:30")
        write_feed(self.fx, "a-eco", [("두바이 쫀득 쿠키 열풍 편의점 품절", t), ("삼성전자 HBM4 양산 돌입", t)])
        write_feed(self.fx, "b-eco", [("두바이 쫀득 쿠키 열풍 편의점 품절", t), ("편의점 디저트 매출 급증", t)])
        write_feed(self.fx, "b-ent", [("아이돌 컴백 쇼케이스 개최", t)])
        s1 = self.run_at("2026-11-02", "10:00")
        self.assertEqual(s1["new_stories"], 4)           # 같은 제목은 한 기사
        n = len(self.state["stories"])
        s2 = self.run_at("2026-11-02", "12:00")          # 같은 피드를 다시 읽어도 늘지 않는다
        self.assertEqual(s2["new_stories"], 0)
        self.assertEqual(len(self.state["stories"]), n)

        wire = [s for s in self.state["stories"].values() if "두바이 쫀득 쿠키" in s["kw"]]
        self.assertEqual(len(wire), 1)                           # 두 매체의 같은 제목은 기사 1건
        self.assertEqual(wire[0]["pubs"], ["a", "b"])            # 매체는 둘로 센다

        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        rows = {r["keyword"]: r for r in store.read_daily(self.data, "2026-11-02")}
        self.assertEqual(rows["편의점"]["articles"], 2)          # 서로 다른 두 기사에 등장
        self.assertEqual(rows["편의점"]["outlets"], 2)           # a, b 두 매체
        self.assertEqual(rows["두바이 쫀득 쿠키"]["articles"], 1)  # 하루 1건뿐인 키워드도 저장한다
        self.assertEqual(rows["두바이 쫀득 쿠키"]["outlets"], 2)   # 두 매체가 같은 기사를 실었다
        self.assertIn("아이돌", rows)

    def test_headline_text_is_not_stored(self):
        t = at("2026-11-02", "09:30")
        write_feed(self.fx, "a-eco", [("절대 저장되면 안 되는 독특한 헤드라인 문장입니다", t)])
        self.run_at("2026-11-02", "10:00")
        p = self.tmp / "state.json.gz"
        save_state(p, self.state)
        import gzip
        raw = gzip.open(p, "rt", encoding="utf-8").read()
        self.assertNotIn("독특한 헤드라인 문장입니다", raw)

    def test_politics_dropped_and_counted(self):
        t = at("2026-11-02", "09:30")
        write_feed(self.fx, "a-eco", [("국회 국정감사 여야 공방 격화", t), ("편의점 디저트 매출 급증", t)])
        s = self.run_at("2026-11-02", "10:00")
        self.assertEqual(s["politics_dropped"], 1)
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        days = quality.read_days(self.data / "days.csv")
        self.assertEqual(days[0]["politics_dropped"], "1")
        self.assertEqual(days[0]["stories"], "1")

    def test_old_items_ignored(self):
        write_feed(self.fx, "a-eco", [("옛날 기사 제목 편의점", at("2026-10-28", "09:00"))])
        s = self.run_at("2026-11-02", "10:00")
        self.assertEqual(s["new_stories"], 0)
        self.assertEqual(s["old_ignored"], 1)

    def test_outage_marks_day_incomplete(self):
        for hh in range(0, 24, 2):                       # 하루 12회 중 피드 2개가 계속 실패
            t = at("2026-11-02", f"{hh:02d}:00")
            write_feed(self.fx, "a-eco", [(f"편의점 신상 디저트 출시 {hh}", t)])
            self.run_at("2026-11-02", f"{hh:02d}:30")
        # 정상일 기록과 비교하기 위해 이 날은 b 피드가 항상 없음 → 3개 중 1개만 정상 = 수집률 0.33
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        day = quality.read_days(self.data / "days.csv")[0]
        self.assertEqual(day["incomplete"], "1")
        self.assertIn("coverage_low", day["flags"])
        self.assertFalse(quality.baseline_status(self.data / "days.csv", CFG)["ready"])

    def test_robots_blocked_feed_not_counted_as_outage(self):
        for fid in ("a-eco", "b-eco"):
            write_feed(self.fx, fid, [("편의점 신상 디저트 출시", at("2026-11-02", "09:30"))])
        (self.fx / "b-ent.robots_blocked").write_text("")
        s = self.run_at("2026-11-02", "10:00")
        self.assertEqual(s["feeds_expected"], 2)
        self.assertEqual(s["feeds_ok"], 2)

    def test_state_reset_flag(self):
        write_feed(self.fx, "a-eco", [("편의점 디저트 신상", at("2026-11-02", "09:30"))])
        for fid in ("b-eco", "b-ent"):
            write_feed(self.fx, fid, [("편의점 디저트 신상", at("2026-11-02", "09:30"))])
        for hh in range(0, 24, 2):
            self.run_at("2026-11-02", f"{hh:02d}:30", reset=(hh == 8))
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        day = quality.read_days(self.data / "days.csv")[0]
        self.assertIn("state_reset", day["flags"])
        self.assertEqual(day["incomplete"], "1")

    def test_open_days_are_not_finalized(self):
        write_feed(self.fx, "a-eco", [("편의점 디저트 신상", at("2026-11-02", "09:30"))])
        self.run_at("2026-11-02", "10:00")
        written = finalize_days(CFG, self.state, at("2026-11-03", "10:00"), self.data, log=lambda *_: None)
        self.assertEqual(written, [])                    # 어제까지는 아직 열려 있다
        written = finalize_days(CFG, self.state, at("2026-11-04", "10:00"), self.data, log=lambda *_: None)
        self.assertTrue(written)

    def test_undated_items_are_not_recounted_on_later_days(self):
        undated = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
                   '<item><title>편의점 디저트 신상 출시</title><link>https://e.com/1</link></item>'
                   '</channel></rss>').encode()
        self.fx.mkdir(parents=True, exist_ok=True)
        (self.fx / "a-eco.xml").write_bytes(undated)
        s1 = self.run_at("2026-11-02", "10:00")
        self.assertEqual(s1["new_stories"], 1)
        self.assertEqual(s1["nodate"], 1)
        s2 = self.run_at("2026-11-03", "10:00")           # 이튿날에도 같은 기사가 목록에 남아 있다
        self.assertEqual(s2["new_stories"], 0)
        s3 = self.run_at("2026-11-05", "10:00")           # 이틀 뒤(상태에서 날이 확정된 뒤)에도 다시 세지 않는다
        self.assertEqual(s3["new_stories"], 0)

    def _day_with_singletons(self, cfg):
        t = at("2026-11-02", "09:30")
        write_feed(self.fx, "a-eco", [("두바이 쫀득 쿠키 열풍 편의점 품절", t), ("편의점 디저트 매출 급증", t)])
        self.run_at("2026-11-02", "10:00")
        finalize_days(cfg, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        return {r["keyword"]: r for r in store.read_daily(self.data, "2026-11-02")}

    def test_single_article_keywords_are_stored_by_default(self):
        self.assertEqual(CFG["min_articles_to_store"], 1)
        rows = self._day_with_singletons(CFG)
        self.assertEqual(rows["두바이"]["articles"], 1)           # 1건
        self.assertEqual(rows["편의점"]["articles"], 2)           # 2건 이상
        self.assertNotIn("존재하지않는키워드", rows)              # 0건은 당연히 없다
        self.assertTrue(all(r["articles"] >= 1 for r in rows.values()))

    def test_store_threshold_still_configurable(self):
        rows = self._day_with_singletons({**CFG, "min_articles_to_store": 2})
        self.assertNotIn("두바이", rows)
        self.assertIn("편의점", rows)

    def test_display_threshold_is_separate_from_storage(self):
        self._day_with_singletons(CFG)
        shown = store.read_daily(self.data, "2026-11-02", min_articles=CFG["min_articles_to_display"])
        keys = {r["keyword"] for r in shown}
        self.assertIn("편의점", keys)
        self.assertNotIn("두바이", keys)                          # 화면에서는 가리지만 파일에는 남아 있다
        self.assertIn("두바이", {r["keyword"] for r in store.read_daily(self.data, "2026-11-02")})

    def test_daily_file_is_gzip_and_legacy_csv_still_readable(self):
        self._day_with_singletons(CFG)
        self.assertTrue((self.data / "daily/2026-11-02.csv.gz").exists())
        self.assertFalse((self.data / "daily/2026-11-02.csv").exists())
        # 예전 형식(날짜.csv, 열 keyword,n,articles,outlets)도 같은 함수로 읽힌다
        (self.data / "daily/2026-10-31.csv").write_text(
            "keyword,n,articles,outlets\n옛키워드,1,5,3\n", encoding="utf-8")
        old = store.read_daily(self.data, "2026-10-31")
        self.assertEqual(old, [{"keyword": "옛키워드", "n": 1, "articles": 5, "outlets": 3}])
        self.assertEqual(store.list_days(self.data), ["2026-10-31", "2026-11-02"])

    def test_first_seen_tracks_low_frequency_keyword(self):
        store.write_daily(self.data, "2026-11-01", [{"keyword": "A", "n": 1, "articles": 1, "outlets": 1}])
        store.write_daily(self.data, "2026-11-02", [{"keyword": "A", "n": 1, "articles": 4, "outlets": 3},
                                                    {"keyword": "B", "n": 1, "articles": 1, "outlets": 1}])
        fs = store.first_seen(self.data)
        self.assertEqual(fs["A"], "2026-11-01")                  # 1건이던 날이 최초 등장일
        self.assertEqual(fs["B"], "2026-11-02")

    def test_rewrite_same_day_replaces_other_format(self):
        row = [{"keyword": "A", "n": 1, "articles": 1, "outlets": 1}]
        store.write_daily(self.data, "2026-11-01", row, gz=False)
        store.write_daily(self.data, "2026-11-01", row, gz=True)
        self.assertEqual(len(list((self.data / "daily").iterdir())), 1)

    def test_late_items_of_a_finalized_day_do_not_overwrite_it(self):
        # 실제로 있었던 문제: 확정한 날의 기사가 36시간 창에 남아 다시 들어와 확정 파일을 덮어썼다
        for fid, title in (("a-eco", "편의점 디저트 신상 출시"), ("b-eco", "삼성전자 HBM4 양산 돌입")):
            write_feed(self.fx, fid, [(title, at("2026-11-02", "14:00")), (title + " 속보", at("2026-11-02", "15:00"))])
        for hh in range(14, 24, 2):
            self.run_at("2026-11-02", f"{hh:02d}:30")
        finalize_days(CFG, self.state, at("2026-11-04", "00:20"), self.data, log=lambda *_: None)
        before = store.read_daily(self.data, "2026-11-02")
        days_before = quality.read_days(self.data / "days.csv")
        self.assertTrue(before)
        # 확정 직후 첫 실행들: 피드에는 아직 11/2 14~15시 기사가 있고 36시간 창 안이다
        s = self.run_at("2026-11-04", "01:30")
        self.assertGreaterEqual(s["closed_ignored"], 2)
        self.assertEqual(s["new_stories"], 0)
        finalize_days(CFG, self.state, at("2026-11-04", "01:40"), self.data, log=lambda *_: None)
        self.assertEqual(store.read_daily(self.data, "2026-11-02"), before)
        self.assertEqual(quality.read_days(self.data / "days.csv"), days_before)

    def test_leftover_stories_of_recorded_day_are_dropped_not_written(self):
        t = at("2026-11-02", "09:30")
        write_feed(self.fx, "a-eco", [("편의점 디저트 신상 출시", t)])
        self.run_at("2026-11-02", "10:00")
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        before = store.read_daily(self.data, "2026-11-02")
        # 예전 버전이 남긴 상태를 흉내: 확정된 날의 기사만 다시 남아 있고 통계는 없다
        self.state["stories"]["2026-11-02:zz"] = {"day": "2026-11-02", "pubs": ["a"], "kw": ["유령"], "p": 0}
        finalize_days(CFG, self.state, at("2026-11-04", "03:00"), self.data, log=lambda *_: None)
        self.assertEqual(store.read_daily(self.data, "2026-11-02"), before)
        self.assertNotIn("2026-11-02:zz", self.state["stories"])

    def test_volume_drop_flagged(self):
        days = [{"date": f"2026-10-{20+i}", "stories": 100, "incomplete": 0} for i in range(5)]
        meta = {"date": "2026-10-30", "stories": 20, "runs": 12, "coverage": 0.95, "reset": False}
        self.assertEqual(quality.evaluate_day(meta, [{k: str(v) for k, v in d.items()} for d in days], CFG),
                         ["volume_low"])


class NotModifiedTests(unittest.TestCase):
    """304(변경 없음) 응답에서도 마지막 기사 시각으로 정지 여부를 판정한다."""
    F = [Feed("a-eco", "a", "A", "economy", "경제", "u1"), Feed("b-eco", "b", "B", "economy", "경제", "u2")]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx, self.data = self.tmp / "fx", self.tmp / "data"
        self.fetcher = FixtureFetcher(self.fx, CFG)
        self.state = new_state()

    def run_at(self, day, hhmm):
        return run_once(CFG, self.F, self.fetcher, self.state, at(day, hhmm), RULES, log=lambda *_: None)

    def feed200(self, fid, day, hhmm, title="편의점 디저트 신상 출시"):
        write_feed(self.fx, fid, [(title, at(day, hhmm))])
        p = self.fx / f"{fid}.not_modified"
        if p.exists():
            p.unlink()

    def feed304(self, fid):
        self.fx.mkdir(parents=True, exist_ok=True)
        (self.fx / f"{fid}.not_modified").write_text("")

    def stale_ids(self, s):
        return [f[0] for f in s["failures"] if f[1] == "stale"]

    def test_normal_304_is_ok(self):
        for fid in ("a-eco", "b-eco"):
            self.feed200(fid, "2026-11-02", "09:00")
        self.run_at("2026-11-02", "10:00")
        for fid in ("a-eco", "b-eco"):
            self.feed304(fid)
        s = self.run_at("2026-11-02", "12:00")
        self.assertEqual(s["statuses"], {"not_modified": 2})
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (2, 2))
        self.assertEqual(s["failures"], [])

    def test_304_within_24h_of_last_article_is_fresh(self):
        self.feed200("a-eco", "2026-11-02", "09:00")
        self.feed200("b-eco", "2026-11-02", "09:00")
        self.run_at("2026-11-02", "10:00")
        self.feed304("a-eco"), self.feed304("b-eco")
        s = self.run_at("2026-11-03", "08:00")                 # 마지막 기사로부터 23시간
        self.assertEqual(s["feeds_ok"], 2)

    def test_304_after_24h_is_stale_and_lowers_coverage(self):
        self.feed200("a-eco", "2026-11-02", "09:00")
        self.feed200("b-eco", "2026-11-02", "09:00")
        self.run_at("2026-11-02", "10:00")
        self.feed304("a-eco")                                   # a 는 304 만 반복, b 는 새 기사를 계속 낸다
        self.feed200("b-eco", "2026-11-03", "11:30", title="다른 디저트 신상 출시")
        s = self.run_at("2026-11-03", "12:00")                 # a: 마지막 기사로부터 27시간
        self.assertEqual(self.stale_ids(s), ["a-eco"])
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (1, 2))
        st = self.state["stats"]["2026-11-03"]
        self.assertEqual(st["stale_runs"], 1)
        self.assertEqual(st["feeds"]["a-eco"]["stale"], 1)
        self.assertEqual(st["feeds"]["a-eco"]["ok"], 0)

    def test_stale_304_feeds_make_the_day_incomplete(self):
        self.feed200("a-eco", "2026-11-01", "09:00")
        self.feed200("b-eco", "2026-11-01", "09:00")
        self.run_at("2026-11-01", "10:00")
        self.feed304("a-eco"), self.feed304("b-eco")
        for hh in range(0, 24, 2):                              # 11/3 하루 종일 두 피드 모두 304 (이미 이틀 전 기사)
            self.run_at("2026-11-03", f"{hh:02d}:30")
        finalize_days(CFG, self.state, at("2026-11-06", "01:00"), self.data, log=lambda *_: None)
        day = [d for d in quality.read_days(self.data / "days.csv") if d["date"] == "2026-11-03"][0]
        self.assertIn("coverage_low", day["flags"])
        self.assertEqual(day["incomplete"], "1")

    def test_state_loss_means_no_stale_judgement(self):
        # 상태가 사라지면 ETag 도 없어 실제로는 200 이 오지만, 304 가 와도 근거 없이 정지로 단정하지 않는다
        self.feed304("a-eco"), self.feed304("b-eco")
        s = self.run_at("2026-11-03", "12:00")
        self.assertEqual(s["feeds_ok"], 2)
        self.assertEqual(s["failures"], [])

    def test_new_200_then_304_uses_the_new_timestamp(self):
        self.feed200("a-eco", "2026-11-01", "08:00")
        self.feed200("b-eco", "2026-11-01", "08:00")
        self.run_at("2026-11-01", "09:00")
        self.feed200("a-eco", "2026-11-03", "08:00", title="새로운 디저트 신상 출시")   # 이틀 뒤 새 기사
        self.feed200("b-eco", "2026-11-03", "08:00", title="새로운 디저트 신상 출시")
        self.run_at("2026-11-03", "09:00")
        self.assertEqual(self.state["http"]["a-eco"]["newest_ts"], at("2026-11-03", "08:00"))
        self.feed304("a-eco"), self.feed304("b-eco")
        s = self.run_at("2026-11-03", "20:00")                 # 새 기사로부터 12시간 → 정상 (예전 기사 시각이었다면 정지)
        self.assertEqual(s["feeds_ok"], 2)

    def test_conditional_validators_are_kept_with_timestamp(self):
        from radar.feeds import FetchResult
        class Fx:
            def fetch(self, feed, now=None, use_conditional=True):
                return FetchResult(feed.id, "ok", http=200, etag='"x"', last_modified="Mon", newest_ts=now - 3600)
        run_once(CFG, self.F[:1], Fx(), self.state, at("2026-11-02", "10:00"), RULES, log=lambda *_: None)
        self.assertEqual(self.state["http"]["a-eco"],
                         {"etag": '"x"', "last_modified": "Mon", "newest_ts": at("2026-11-02", "10:00") - 3600})


class DeferredPruneAndIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx, self.data = self.tmp / "fx", self.tmp / "data"
        self.fetcher = FixtureFetcher(self.fx, CFG)
        self.state = new_state()

    def run_at(self, day, hhmm):
        return run_once(CFG, FEEDS, self.fetcher, self.state, at(day, hhmm), RULES, log=lambda *_: None)

    def seed_two_days(self):
        write_feed(self.fx, "a-eco", [
            ("두바이 쫀득 쿠키 열풍 편의점 품절", at("2026-11-02", "09:30")),
            ("두바이쫀득쿠키 인기에 디저트 호황", at("2026-11-02", "10:00")),
        ])
        write_feed(self.fx, "b-eco", [("편의점 디저트 매출 급증", at("2026-11-02", "10:30"))])
        self.run_at("2026-11-02", "11:00")

        write_feed(self.fx, "a-eco", [("삼성전자 HBM4 양산 돌입", at("2026-11-03", "09:30"))])
        write_feed(self.fx, "b-eco", [("전기차 보조금 확대 발표", at("2026-11-03", "10:00"))])
        self.run_at("2026-11-03", "11:00")

    def test_prune_true_vs_false_for_closed_day(self):
        self.seed_two_days()
        now = at("2026-11-04", "01:00")  # 11/2는 닫힌 날, 11/3은 열린 날

        import copy
        state_prune = copy.deepcopy(self.state)
        data_prune = self.tmp / "data_prune"
        w1 = finalize_days(CFG, state_prune, now, data_prune, prune=True, log=lambda *_: None)
        self.assertTrue(w1)
        self.assertFalse(any(v["day"] == "2026-11-02" for v in state_prune["stories"].values()))
        self.assertNotIn("2026-11-02", state_prune["stats"])
        self.assertTrue(any(v["day"] == "2026-11-03" for v in state_prune["stories"].values()))

        state_defer = copy.deepcopy(self.state)
        data_defer = self.tmp / "data_defer"
        w2 = finalize_days(CFG, state_defer, now, data_defer, prune=False, log=lambda *_: None)
        self.assertEqual(w2, w1)
        self.assertTrue(any(v["day"] == "2026-11-02" for v in state_defer["stories"].values()))
        self.assertIn("2026-11-02", state_defer["stats"])
        self.assertTrue(any(v["day"] == "2026-11-03" for v in state_defer["stories"].values()))

    def test_prune_finalized_days_only_prunes_verified_days_and_keeps_open_days(self):
        self.seed_two_days()
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, prune=False, log=lambda *_: None)
        pruned = prune_finalized_days(self.state, self.data, log=lambda *_: None)
        self.assertEqual(pruned, ["2026-11-02"])
        self.assertFalse(any(v["day"] == "2026-11-02" for v in self.state["stories"].values()))
        self.assertNotIn("2026-11-02", self.state["stats"])
        self.assertTrue(any(v["day"] == "2026-11-03" for v in self.state["stories"].values()))
        self.assertIn("2026-11-03", self.state["stats"])

    def test_prune_finalized_days_skips_incomplete_disk_artifacts(self):
        self.seed_two_days()
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, prune=False, log=lambda *_: None)

        # 1) health/{day}.csv 누락 (runs > 0 인데 없음)
        health_file = self.data / "health" / "2026-11-02.csv"
        health_bytes = health_file.read_bytes()
        health_file.unlink()
        self.assertEqual(prune_finalized_days(self.state, self.data, log=lambda *_: None), [])
        self.assertIn("2026-11-02", self.state["stats"])
        health_file.write_bytes(health_bytes)

        # 2) daily/{day} 누락
        daily_file = store.find_daily(self.data, "2026-11-02")
        daily_bytes = daily_file.read_bytes()
        daily_file.unlink()
        self.assertEqual(prune_finalized_days(self.state, self.data, log=lambda *_: None), [])
        self.assertIn("2026-11-02", self.state["stats"])
        daily_file.write_bytes(daily_bytes)

        # 3) days.csv 행 누락
        days_file = self.data / "days.csv"
        days_bytes = days_file.read_bytes()
        days_file.write_text("date,stories\n", encoding="utf-8")
        self.assertEqual(prune_finalized_days(self.state, self.data, log=lambda *_: None), [])
        self.assertIn("2026-11-02", self.state["stats"])
        days_file.write_bytes(days_bytes)

        # 모두 갖춰지면 정상 정리
        self.assertEqual(prune_finalized_days(self.state, self.data, log=lambda *_: None), ["2026-11-02"])

    def test_push_failure_recovery_with_fresh_data_dir(self):
        self.seed_two_days()
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, prune=False, log=lambda *_: None)
        # push 실패 시 다음 실행은 직전 데이터 커밋이 없는 새 data 폴더 + 미정리 상태 캐시로 시작한다
        fresh_data = self.tmp / "fresh_data"
        written = finalize_days(CFG, self.state, at("2026-11-04", "02:00"), fresh_data, prune=False, log=lambda *_: None)
        self.assertIn("daily/2026-11-02.csv.gz", written)
        self.assertTrue((fresh_data / "health" / "2026-11-02.csv").exists())
        self.assertIn("2026-11-02", {r["date"] for r in quality.read_days(fresh_data / "days.csv")})
        pruned = prune_finalized_days(self.state, fresh_data, log=lambda *_: None)
        self.assertEqual(pruned, ["2026-11-02"])
        self.assertNotIn("2026-11-02", self.state["stats"])

    def test_refinalizing_recorded_day_skips_overwrite_and_preserves_files(self):
        self.seed_two_days()
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, prune=False, log=lambda *_: None)
        daily_before = (self.data / "daily" / "2026-11-02.csv.gz").read_bytes()
        days_before = (self.data / "days.csv").read_bytes()
        log_before = (self.data / "aliases" / "merge_log.csv").read_bytes()

        # prune=False 재확정: 덮어쓰지 않고 상태도 유지
        logs_defer = []
        written1 = finalize_days(CFG, self.state, at("2026-11-04", "02:00"), self.data, prune=False, log=logs_defer.append)
        self.assertEqual(written1, [])
        self.assertIn("2026-11-02", self.state["stats"])
        self.assertEqual((self.data / "daily" / "2026-11-02.csv.gz").read_bytes(), daily_before)
        self.assertEqual((self.data / "days.csv").read_bytes(), days_before)
        self.assertEqual((self.data / "aliases" / "merge_log.csv").read_bytes(), log_before)

        # prune=True 재확정: 덮어쓰지 않으면서 상태만 정리
        written2 = finalize_days(CFG, self.state, at("2026-11-04", "03:00"), self.data, prune=True, log=lambda *_: None)
        self.assertEqual(written2, [])
        self.assertNotIn("2026-11-02", self.state["stats"])
        self.assertFalse(any(v["day"] == "2026-11-02" for v in self.state["stories"].values()))
        self.assertEqual((self.data / "daily" / "2026-11-02.csv.gz").read_bytes(), daily_before)
        self.assertEqual((self.data / "days.csv").read_bytes(), days_before)
        self.assertEqual((self.data / "aliases" / "merge_log.csv").read_bytes(), log_before)

    def test_repeated_write_day_is_byte_identical(self):
        # space/case 병합과 initials(3어절 + 1어절 줄임말) 모두 발생하도록 구성
        self.state["stories"] = {
            "2026-11-02:1": {"day": "2026-11-02", "pubs": ["a"], "kw": ["두바이 쫀득 쿠키", "ai"], "p": 0},
            "2026-11-02:2": {"day": "2026-11-02", "pubs": ["a"], "kw": ["두바이 쫀득 쿠키", "AI"], "p": 0},
            "2026-11-02:3": {"day": "2026-11-02", "pubs": ["b"], "kw": ["두바이 쫀득 쿠키", "AI"], "p": 0},
            "2026-11-02:4": {"day": "2026-11-02", "pubs": ["b"], "kw": ["두바이쫀득쿠키"], "p": 0},
            "2026-11-02:5": {"day": "2026-11-02", "pubs": ["a"], "kw": ["두쫀쿠"], "p": 0},
            "2026-11-02:6": {"day": "2026-11-02", "pubs": ["b"], "kw": ["두쫀쿠"], "p": 0},
            "2026-11-02:7": {"day": "2026-11-02", "pubs": ["a"], "kw": ["두쫀쿠"], "p": 0},
        }
        self.state["stats"]["2026-11-02"] = {
            "runs": 2, "expected_sum": 6.0, "ok_sum": 6.0, "stale_runs": 0, "reset": False,
            "feeds": {"a-eco": {"runs": 2, "ok": 2, "stale": 0, "items_new": 4, "last_status": "ok", "last_error": ""}},
        }
        write_day("2026-11-02", self.state, CFG, self.data, log=lambda *_: None)
        files = [
            self.data / "daily" / "2026-11-02.csv.gz",
            self.data / "health" / "2026-11-02.csv",
            self.data / "days.csv",
            self.data / "aliases" / "merge_log.csv",
            self.data / "aliases" / "canon.json",
            self.data / "aliases" / "alias_map.json",
        ]
        for p in files:
            self.assertTrue(p.exists(), str(p))
        first_bytes = {p: p.read_bytes() for p in files}

        write_day("2026-11-02", self.state, CFG, self.data, log=lambda *_: None)
        for p in files:
            self.assertEqual(p.read_bytes(), first_bytes[p], f"Changed after repeated write_day: {p.name}")

    def test_save_state_cleans_up_tmp_and_preserves_existing_file_on_failure(self):
        state_path = self.tmp / "state" / "state.json.gz"
        tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        self.state["meta"]["marker"] = "original"
        save_state(state_path, self.state)
        original_bytes = state_path.read_bytes()

        # 1) 직렬화/쓰기 중 실패
        new_st = new_state()
        new_st["meta"]["marker"] = "broken"
        with patch("radar.state.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_state(state_path, new_st)
        self.assertFalse(tmp_path.exists())
        self.assertEqual(state_path.read_bytes(), original_bytes)

        # 2) os.replace 단계 실패
        with patch("radar.state.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_state(state_path, new_st)
        self.assertFalse(tmp_path.exists())
        self.assertEqual(state_path.read_bytes(), original_bytes)
        loaded, missing = load_state(state_path)
        self.assertFalse(missing)
        self.assertEqual(loaded["meta"]["marker"], "original")


if __name__ == "__main__":
    unittest.main()
