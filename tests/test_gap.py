"""수집 공백(max_gap_h) 계산과 gap_long / gap_unknown 판정."""
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
from radar.state import new_state  # noqa: E402
from radar.util import load_settings, read_wordlist  # noqa: E402

CFG = load_settings()
RULES = Rules.from_lists(read_wordlist(ROOT / "config/stopwords.txt"), read_wordlist(ROOT / "config/politics_terms.txt"))
FEEDS = [Feed("a-eco", "a", "A", "economy", "경제", "u1")]


def at(day, hhmm):
    import datetime as dt
    h, m = hhmm.split(":")
    return dt.datetime.fromisoformat(f"{day}T{h}:{m}:00+09:00").timestamp()


class MaxGapTests(unittest.TestCase):
    def test_regular_hourly(self):
        ts = [at("2026-11-01", "23:30")] + [at("2026-11-02", f"{h:02d}:30") for h in range(24)] + [at("2026-11-03", "00:30")]
        self.assertAlmostEqual(quality.max_gap_hours(ts, "2026-11-02"), 1.0, places=2)

    def test_long_gap_inside_the_day(self):
        ts = [at("2026-11-01", "23:00"), at("2026-11-02", "01:00"), at("2026-11-02", "08:00"), at("2026-11-02", "09:00"),
              at("2026-11-02", "23:00"), at("2026-11-03", "00:30")]
        self.assertAlmostEqual(quality.max_gap_hours(ts, "2026-11-02"), 14.0, places=2)   # 09:00 → 23:00

    def test_gap_across_midnight_counts_for_both_days(self):
        # 11/1 21:00 에 한 번 돌고 11/2 04:00 에 다시 돎 → 7시간 공백이 두 날 모두에 걸친다
        ts = ([at("2026-10-31", "23:30")] + [at("2026-11-01", f"{h:02d}:00") for h in range(0, 22)] +
              [at("2026-11-02", "04:00")] + [at("2026-11-02", f"{h:02d}:30") for h in range(5, 24)] + [at("2026-11-03", "00:30")])
        self.assertAlmostEqual(quality.max_gap_hours(ts, "2026-11-01"), 7.0, places=2)
        self.assertAlmostEqual(quality.max_gap_hours(ts, "2026-11-02"), 7.0, places=2)

    def test_no_earlier_run_counts_from_midnight(self):
        ts = [at("2026-11-02", "08:00"), at("2026-11-02", "09:00"), at("2026-11-03", "00:10")]
        self.assertAlmostEqual(quality.max_gap_hours(ts, "2026-11-02"), 15 + 10 / 60, places=2)   # 09:00 → 다음 날 00:10
        self.assertAlmostEqual(quality.max_gap_hours([at("2026-11-02", "08:00"), at("2026-11-02", "09:00"),
                                                       at("2026-11-02", "10:00"), at("2026-11-03", "00:10")],
                                                      "2026-11-02"), 14 + 10 / 60, places=2)

    def test_no_runs_at_all_is_a_full_day_gap(self):
        self.assertAlmostEqual(quality.max_gap_hours([], "2026-11-02"), 24.0, places=2)


class FlagTests(unittest.TestCase):
    base = {"date": "2026-11-02", "stories": 100, "runs": 12, "coverage": 1.0, "reset": False}

    def test_gap_long_only_above_threshold(self):
        self.assertEqual(quality.evaluate_day({**self.base, "max_gap_h": 6.0}, [], CFG), [])
        self.assertEqual(quality.evaluate_day({**self.base, "max_gap_h": 6.1}, [], CFG), ["gap_long"])

    def test_gap_unknown_when_recording_started_late(self):
        self.assertEqual(quality.evaluate_day({**self.base, "max_gap_h": ""}, [], CFG), ["gap_unknown"])

    def test_old_meta_without_the_field_is_unaffected(self):
        self.assertEqual(quality.evaluate_day(dict(self.base), [], CFG), [])


class PipelineGapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx, self.data = self.tmp / "fx", self.tmp / "data"
        self.fetcher = FixtureFetcher(self.fx, CFG)
        self.state = new_state()

    def run_at(self, day, hhmm):
        now = at(day, hhmm)
        write_feed(self.fx, "a-eco", [("편의점 디저트 신상 출시", now - 600)])
        return run_once(CFG, FEEDS, self.fetcher, self.state, now, RULES, log=lambda *_: None)

    def test_run_times_recorded_and_pruned_after_five_days(self):
        self.run_at("2026-11-01", "10:00")
        self.run_at("2026-11-01", "12:00")
        self.assertEqual(self.state["meta"]["run_ts_start"], at("2026-11-01", "10:00"))
        self.assertEqual(len(self.state["meta"]["run_ts"]), 2)
        self.run_at("2026-11-08", "10:00")                       # 5일을 넘긴 기록은 버린다. 시작 시각은 유지
        self.assertEqual(self.state["meta"]["run_ts"], [at("2026-11-08", "10:00")])
        self.assertEqual(self.state["meta"]["run_ts_start"], at("2026-11-01", "10:00"))

    def test_finalized_day_gets_gap_or_unknown(self):
        for hh in ("22:30",):
            self.run_at("2026-11-01", hh)                         # 기록 시작 (11/1 은 0시부터 알 수 없음)
        for hh in ("01:30", "09:30", "10:30", "20:30"):
            self.run_at("2026-11-02", hh)                         # 10:30 → 20:30 = 10시간 공백
        self.run_at("2026-11-03", "00:30")
        finalize_days(CFG, self.state, at("2026-11-04", "01:00"), self.data, log=lambda *_: None)
        days = {d["date"]: d for d in quality.read_days(self.data / "days.csv")}
        self.assertEqual(days["2026-11-01"]["max_gap_h"], "")                 # 기록 시작 전의 날 → 알 수 없음
        self.assertIn("gap_unknown", days["2026-11-01"]["flags"])
        self.assertEqual(days["2026-11-02"]["max_gap_h"], "10.0")
        self.assertIn("gap_long", days["2026-11-02"]["flags"])
        self.assertEqual(days["2026-11-02"]["incomplete"], "1")


if __name__ == "__main__":
    unittest.main()
