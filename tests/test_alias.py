import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import alias, quality  # noqa: E402
from radar.util import load_settings  # noqa: E402

CFG = load_settings()


class AliasTests(unittest.TestCase):
    def test_space_and_case_merge_with_stable_registry(self):
        reg = {}
        kw = [["두바이쫀득쿠키"], ["두바이 쫀득 쿠키"], ["두바이 쫀득 쿠키"], ["ai"], ["AI"], ["AI"]]
        mapping, logs = alias.canonicalize(kw, reg)
        self.assertEqual(mapping["두바이쫀득쿠키"], "두바이 쫀득 쿠키")
        self.assertEqual(mapping["ai"], "AI")
        self.assertEqual({l["rule"] for l in logs}, {"space", "case"})
        # 다음 날 변이형 표기만 나와도 같은 대표 표기를 쓴다
        mapping2, _ = alias.canonicalize([["두바이쫀득쿠키"], ["두바이쫀득쿠키"]], reg)
        self.assertEqual(mapping2["두바이쫀득쿠키"], "두바이 쫀득 쿠키")

    def test_no_merge_for_single_form(self):
        mapping, logs = alias.canonicalize([["편의점"], ["편의점"]], {})
        self.assertEqual(mapping, {})
        self.assertEqual(logs, [])

    def rows(self, ini_articles=4, ini_outlets=3):
        return [{"keyword": "두바이 쫀득 쿠키", "n": 3, "articles": 8, "outlets": 4},
                {"keyword": "두쫀쿠", "n": 1, "articles": ini_articles, "outlets": ini_outlets}]

    def test_initials_detected(self):
        found = alias.detect_initials(self.rows(), {}, CFG)
        self.assertEqual(found[0]["alias"], "두쫀쿠")
        self.assertEqual(found[0]["canonical"], "두바이 쫀득 쿠키")

    def test_initials_need_evidence(self):
        self.assertEqual(alias.detect_initials(self.rows(ini_outlets=1), {}, CFG), [])
        self.assertEqual(alias.detect_initials(self.rows(ini_articles=2), {}, CFG), [])
        self.assertEqual(alias.detect_initials(self.rows(), {"두쫀쿠": "이미"}, CFG), [])

    def test_apply_alias_map_sums_counts(self):
        merged = alias.apply_alias_map(self.rows(), {"두쫀쿠": "두바이 쫀득 쿠키"})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["articles"], 12)


class BaselineTests(unittest.TestCase):
    def test_baseline_needs_21_complete_days(self):
        import tempfile
        p = Path(tempfile.mkdtemp()) / "days.csv"
        rows = [{"date": f"2026-10-{d:02d}", "incomplete": 0, "stories": 100} for d in range(1, 21)]
        quality.write_days(p, rows)
        self.assertFalse(quality.baseline_status(p, CFG)["ready"])
        rows.append({"date": "2026-10-21", "incomplete": 1, "stories": 5, "flags": "coverage_low"})
        quality.write_days(p, rows)
        self.assertFalse(quality.baseline_status(p, CFG)["ready"])   # 불완전한 날은 세지 않는다
        rows.append({"date": "2026-10-22", "incomplete": 0, "stories": 100})
        quality.write_days(p, rows)
        st = quality.baseline_status(p, CFG)
        self.assertTrue(st["ready"])
        self.assertEqual(st["complete_days"], 21)


if __name__ == "__main__":
    unittest.main()
