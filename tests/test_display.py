import ast
import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import display, store  # noqa: E402

CFG = display.load_config()


def row(k, n, o=3):
    return {"keyword": k, "n": n, "articles": n, "outlets": o}


def tree_hash(path: Path) -> dict:
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


class PlaceTests(unittest.TestCase):
    P = {"뉴욕", "순천", "독일", "일본"}

    def test_strips_only_allowlisted_place(self):
        self.assertEqual(display.normalize_place("뉴욕서", self.P), "뉴욕")
        self.assertEqual(display.normalize_place("순천서 화재", self.P), "순천 화재")

    def test_keeps_words_where_seo_is_part_of_the_word(self):
        # 어간이 다른 키워드로 존재할 수 있는 단어는 그대로 둔다 (보고서→보고 가 되면 안 됨)
        for w in ("보고서", "청구서", "인플루언서", "해변서", "공항서", "시장서"):
            self.assertEqual(display.normalize_place(w, self.P), w)

    def test_single_char_and_bare_seo(self):
        self.assertEqual(display.normalize_place("서", self.P), "서")
        self.assertEqual(display.normalize_place("서울", self.P), "서울")

    def test_shipped_allowlist_excludes_common_nouns(self):
        places = CFG["_places"]
        for w in ("경기", "시장", "공항", "해변", "터널", "보고", "청구"):
            self.assertNotIn(w, places)
        self.assertIn("뉴욕", places)


class GroupTests(unittest.TestCase):
    def view(self, rows, **kw):
        return display.build_view(rows, CFG, **kw)

    def test_equal_counts_group_with_longest_as_label(self):
        v = self.view([row("공매도", 4), row("불법 공매도", 4)])
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["label"], "불법 공매도")
        self.assertEqual({m["keyword"] for m in v[0]["members"]}, {"공매도", "불법 공매도"})
        self.assertEqual(v[0]["articles"], 4)  # 합(8)이 아니라 최댓값

    def test_near_equal_and_distinct_are_not_grouped(self):
        v = self.view([row("이범호", 19), row("이범호 감독", 17), row("해킹", 45), row("해킹 배후", 17)])
        self.assertEqual(len(v), 4)

    def test_phrase_joins_one_group_only(self):
        # 홀케이크 4종 은 두 구성 단어와 모두 4건: 한 그룹에만 속하고 항목이 중복되지 않는다
        v = self.view([row("홀케이크", 4, 3), row("4종", 4, 2), row("홀케이크 4종", 4)])
        kws = [m["keyword"] for it in v for m in it["members"]]
        self.assertEqual(sorted(kws), sorted(set(kws)))
        self.assertEqual(sorted(kws), ["4종", "홀케이크", "홀케이크 4종"])

    def test_group_never_replaces_or_merges_member_counts(self):
        rows = [row("공매도", 4), row("불법 공매도", 4)]
        v = self.view(rows)[0]
        self.assertEqual({m["keyword"]: m["articles"] for m in v["members"]}, {"공매도": 4, "불법 공매도": 4})

    def test_input_rows_not_mutated(self):
        rows = [row("공매도", 4), row("불법 공매도", 4), row("뉴욕서", 3)]
        before = copy.deepcopy(rows)
        self.view(rows)
        self.assertEqual(rows, before)

    def test_min_articles_applied_at_view_time(self):
        v = self.view([row("a", 1), row("b", 2)], min_articles=2)
        self.assertEqual([it["label"] for it in v], ["b"])

    def test_place_label_merge_keeps_both_raw_keywords_without_summing(self):
        v = self.view([row("뉴욕", 4), row("뉴욕서", 3)])
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["articles"], 4)
        self.assertEqual({m["keyword"] for m in v[0]["members"]}, {"뉴욕", "뉴욕서"})


class SingleOutletTests(unittest.TestCase):
    def test_demoted_not_removed(self):
        rows = [row("PO5", 33, 1), row("트럼프", 20, 11), row("박승규", 12, 1)]
        v = display.build_view(rows, CFG)
        labels = [it["label"] for it in v]
        self.assertEqual(labels[0], "트럼프")  # 33*0.25=8.25 < 20
        self.assertIn("PO5", labels)
        self.assertIn("박승규", labels)
        self.assertIn("single_outlet", next(i for i in v if i["label"] == "PO5")["flags"])
        self.assertNotIn("single_outlet", next(i for i in v if i["label"] == "트럼프")["flags"])

    def test_below_threshold_not_flagged(self):
        v = display.build_view([row("작은일", 7, 1)], CFG)
        self.assertEqual(v[0]["flags"], [])


class IsolationAndRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for day, n in (("2026-11-01", 4), ("2026-11-02", 6), ("2026-11-03", 5)):
            store.write_daily(self.tmp, day, [row("공매도", n), row("불법 공매도", n), row("뉴욕서", 3), row("PO5", 9, 1)])

    def test_display_never_changes_stored_files(self):
        before = tree_hash(self.tmp)
        v = display.day_view(self.tmp, "2026-11-02")
        display.member_series(self.tmp, v[0], ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(tree_hash(self.tmp), before)

    def test_stored_rows_identical_with_and_without_display(self):
        raw1 = store.read_daily(self.tmp, "2026-11-02")
        display.day_view(self.tmp, "2026-11-02")
        raw2 = store.read_daily(self.tmp, "2026-11-02")
        self.assertEqual(raw1, raw2)
        self.assertEqual({r["keyword"] for r in raw2}, {"공매도", "불법 공매도", "뉴욕서", "PO5"})

    def test_member_series_keeps_each_keyword_series(self):
        v = display.day_view(self.tmp, "2026-11-02")
        grp = next(i for i in v if i["label"] == "불법 공매도")
        s = display.member_series(self.tmp, grp, ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(s["공매도"], {"2026-11-01": 4, "2026-11-02": 6, "2026-11-03": 5})
        self.assertEqual(s["불법 공매도"], s["공매도"])

    def test_member_series_does_not_substitute_the_short_keyword(self):
        # 어느 날 짧은 키워드만 늘면(구절은 그대로) 그룹이 깨져도 각 키워드의 값은 그대로 읽힌다
        store.write_daily(self.tmp, "2026-11-04", [row("공매도", 9), row("불법 공매도", 4)])
        v = display.day_view(self.tmp, "2026-11-04")
        self.assertEqual(len(v), 2)
        grp = next(i for i in v if i["label"] == "공매도")
        s = display.member_series(self.tmp, grp, ["2026-11-04"])
        self.assertEqual(s, {"공매도": {"2026-11-04": 9}})

    def test_pipeline_and_analysis_modules_do_not_import_display(self):
        offenders = []
        files = list((ROOT / "radar").glob("*.py")) + [ROOT / "scripts" / n for n in ("collect.py", "status.py", "simulate.py", "probe_feeds.py")]
        for p in files:
            if p.name == "display.py":
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                if any(n == "display" or n.endswith(".display") for n in names):
                    offenders.append(p.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
