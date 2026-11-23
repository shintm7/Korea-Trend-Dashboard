import csv
import sys
import tempfile
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


class AppendLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "aliases" / "merge_log.csv"

    def entry(self, rule="space", alias_name="두바이쫀득쿠키", canonical="두바이 쫀득 쿠키", a_art=2, c_art=5):
        return {
            "rule": rule,
            "alias": alias_name,
            "canonical": canonical,
            "alias_articles": a_art,
            "canonical_articles": c_art,
        }

    def read_rows(self):
        with open(self.log, encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            return rd.fieldnames, list(rd)

    def test_empty_entries_does_not_create_file(self):
        alias.append_log(self.log, "2026-11-02", [])
        self.assertFalse(self.log.exists())

    def test_new_file_creates_header_and_rows(self):
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        header, rows = self.read_rows()
        self.assertEqual(header, alias.LOG_FIELDS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-02")
        self.assertEqual(rows[0]["alias"], "두바이쫀득쿠키")

    def test_append_new_rows_to_existing_file(self):
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        alias.append_log(self.log, "2026-11-03", [self.entry(rule="case", alias_name="ai", canonical="AI")])
        _, rows = self.read_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([(r["date"], r["alias"]) for r in rows], [("2026-11-02", "두바이쫀득쿠키"), ("2026-11-03", "ai")])

    def test_header_only_and_zero_byte_files(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_bytes(b"")
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        header, rows = self.read_rows()
        self.assertEqual(header, alias.LOG_FIELDS)
        self.assertEqual(len(rows), 1)

        header_only = self.tmp / "header_only.csv"
        header_only.write_text(",".join(alias.LOG_FIELDS) + "\n", encoding="utf-8")
        alias.append_log(header_only, "2026-11-02", [self.entry()])
        with open(header_only, encoding="utf-8", newline="") as f:
            rows2 = list(csv.DictReader(f))
        self.assertEqual(len(rows2), 1)
        self.assertEqual(header_only.read_text(encoding="utf-8").splitlines()[0], ",".join(alias.LOG_FIELDS))

    def test_files_without_trailing_newline(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        # 헤더만 있고 끝 줄바꿈이 없는 파일
        self.log.write_bytes((",".join(alias.LOG_FIELDS)).encode("utf-8"))
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        header, rows = self.read_rows()
        self.assertEqual(header, alias.LOG_FIELDS)
        self.assertEqual(len(rows), 1)

        # 행은 있는데 끝 줄바꿈이 없는 파일에 중복만 넣으면 바이트가 그대로다
        no_nl = self.tmp / "no_nl.csv"
        raw_content = (
            ",".join(alias.LOG_FIELDS)
            + "\n2026-11-02,space,두바이쫀득쿠키,두바이 쫀득 쿠키,2,5"
        ).encode("utf-8")
        no_nl.write_bytes(raw_content)
        alias.append_log(no_nl, "2026-11-02", [self.entry()])
        self.assertEqual(no_nl.read_bytes(), raw_content)

        # 새 행을 추가하면 개행을 넣은 뒤 정상적으로 붙는다
        alias.append_log(no_nl, "2026-11-02", [self.entry(rule="case", alias_name="ai", canonical="AI")])
        with open(no_nl, encoding="utf-8", newline="") as f:
            rows2 = list(csv.DictReader(f))
        self.assertEqual(len(rows2), 2)
        self.assertEqual(rows2[1]["alias"], "ai")

    def test_repeated_identical_entries_leave_bytes_unchanged(self):
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        before = self.log.read_bytes()
        alias.append_log(self.log, "2026-11-02", [self.entry()])
        self.assertEqual(self.log.read_bytes(), before)

    def test_duplicate_keys_within_single_batch_keeps_first(self):
        batch = [self.entry(a_art=2, c_art=5), self.entry(a_art=9, c_art=99)]
        alias.append_log(self.log, "2026-11-02", batch)
        _, rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["alias_articles"], rows[0]["canonical_articles"]), ("2", "5"))

    def test_partial_overlap_appends_only_new_keys(self):
        e1 = self.entry()
        e2 = self.entry(rule="case", alias_name="ai", canonical="AI", a_art=3, c_art=7)
        alias.append_log(self.log, "2026-11-02", [e1])
        alias.append_log(self.log, "2026-11-02", [e1, e2])
        _, rows = self.read_rows()
        self.assertEqual([(r["rule"], r["alias"]) for r in rows], [("space", "두바이쫀득쿠키"), ("case", "ai")])

    def test_existing_key_with_different_article_counts_not_overwritten(self):
        alias.append_log(self.log, "2026-11-02", [self.entry(a_art=1, c_art=3)])
        before = self.log.read_bytes()
        alias.append_log(self.log, "2026-11-02", [self.entry(a_art=42, c_art=99)])
        self.assertEqual(self.log.read_bytes(), before)
        _, rows = self.read_rows()
        self.assertEqual((rows[0]["alias_articles"], rows[0]["canonical_articles"]), ("1", "3"))

    def test_historical_duplicate_rows_remain_untouched_byte_for_byte(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        hist = (
            "date,rule,alias,canonical,alias_articles,canonical_articles\n"
            "2026-11-01,space,두바이쫀득쿠키,두바이 쫀득 쿠키,1,2\n"
            "2026-11-01,space,두바이쫀득쿠키,두바이 쫀득 쿠키,3,4\n"
        ).encode("utf-8")
        self.log.write_bytes(hist)
        # 기존 키 중복만 전달 시 파일 전체가 바이트 단위로 유지된다
        alias.append_log(self.log, "2026-11-01", [self.entry(a_art=10, c_art=20)])
        self.assertEqual(self.log.read_bytes(), hist)
        # 새 키를 추가해도 기존 이력 중복 바이트는 앞부분에 그대로 보존된다
        alias.append_log(self.log, "2026-11-02", [self.entry(rule="case", alias_name="ai", canonical="AI")])
        after = self.log.read_bytes()
        self.assertTrue(after.startswith(hist))
        _, rows = self.read_rows()
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
