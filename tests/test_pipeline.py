import csv
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import write_feed  # noqa: E402
from radar import quality  # noqa: E402
from radar.extract import Rules  # noqa: E402
from radar.feeds import Feed, FixtureFetcher  # noqa: E402
from radar.pipeline import finalize_days, run_once  # noqa: E402
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
        rows = {r["keyword"]: r for r in read_rows(self.data / "daily/2026-11-02.csv")}
        self.assertEqual(rows["편의점"]["articles"], "2")        # 서로 다른 두 기사에 등장
        self.assertEqual(rows["편의점"]["outlets"], "2")         # a, b 두 매체
        self.assertNotIn("두바이 쫀득 쿠키", rows)               # 하루 1건뿐인 키워드는 저장하지 않는다
        self.assertNotIn("아이돌", rows)

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

    def test_volume_drop_flagged(self):
        days = [{"date": f"2026-10-{20+i}", "stories": 100, "incomplete": 0} for i in range(5)]
        meta = {"date": "2026-10-30", "stories": 20, "runs": 12, "coverage": 0.95, "reset": False}
        self.assertEqual(quality.evaluate_day(meta, [{k: str(v) for k, v in d.items()} for d in days], CFG),
                         ["volume_low"])


if __name__ == "__main__":
    unittest.main()
