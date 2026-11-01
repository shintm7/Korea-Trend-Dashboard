"""scripts/collect.py 를 별도 프로세스로 실행해 실제 수집 경로 전체를 검증한다.

단위 테스트(run_once/finalize_days 직접 호출)와 달리, 여기서는 CLI 인자 처리 → 상태 파일 저장·복원(프로세스 사이) →
일일 파일 생성(csv.gz) → last_run.json 까지 임시 폴더에서 실제로 실행하고, 만들어진 파일을 store.py 를 쓰지 않고
gzip/csv 로 직접 열어 확인한다. 네트워크는 쓰지 않는다 (로컬 서버 또는 fixture).
"""
import csv
import datetime as dt
import gzip
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import rss, write_feed  # noqa: E402
from radar import quality  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


def at(day, hhmm):
    return dt.datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").timestamp()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_gz_csv(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        return rd.fieldnames, list(rd)


class Env:
    """임시 폴더에 설정·피드·데이터·상태를 만들고 collect.py 를 실행한다."""

    def __init__(self, feeds):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
        cfg.update({"host_min_interval_sec": 0, "retries": 0, "timeout_sec": 5})
        self.cfg_path = self.tmp / "settings.yaml"
        self.cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
        self.feeds_path = self.tmp / "feeds.yaml"
        self.feeds_path.write_text(yaml.safe_dump({"feeds": feeds}, allow_unicode=True), encoding="utf-8")
        self.data, self.state, self.fx = self.tmp / "data", self.tmp / "state", self.tmp / "fx"

    def collect(self, now, fixtures=True, expect_rc=0):
        cmd = [sys.executable, str(ROOT / "scripts" / "collect.py"), "--config", str(self.cfg_path),
               "--feeds", str(self.feeds_path), "--data-dir", str(self.data), "--state-dir", str(self.state),
               "--now", now]
        if fixtures:
            cmd += ["--fixtures", str(self.fx)]
        env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
        assert p.returncode == expect_rc, f"rc={p.returncode}\n{p.stdout}\n{p.stderr}"
        return json.loads((self.state / "last_run.json").read_text(encoding="utf-8")), p.stdout


def feed(fid, url="https://example.com/x"):
    return {"id": fid, "pub": fid.split("-")[0], "name": fid, "section": "economy", "title": "경제",
            "url": url, "enabled": True}


class CollectPathTests(unittest.TestCase):
    def test_daily_gz_written_by_collect_and_finalized_day_not_overwritten(self):
        env = Env([feed("a-eco"), feed("b-eco")])
        t = at("2026-11-02", "09:30")
        write_feed(env.fx, "a-eco", [("편의점 디저트 신상 출시", t), ("삼성전자 HBM4 양산 돌입", t)])
        write_feed(env.fx, "b-eco", [("편의점 디저트 매출 급증", t)])

        # 예전 형식으로 이미 확정된 날 (불필요하게 덮어써지면 안 된다)
        legacy = env.data / "daily" / "2026-10-31.csv"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("keyword,n,articles,outlets\n옛키워드,1,5,3\n", encoding="utf-8")
        quality.upsert_day(env.data / "days.csv", {"date": "2026-10-31", "stories": 100, "runs": 12,
                                                    "coverage": 1.0, "incomplete": 0, "flags": ""})
        legacy_hash, days_before = sha(legacy), (env.data / "days.csv").read_text(encoding="utf-8")

        env.collect("2026-11-02T10:00:00+09:00")
        self.assertFalse((env.data / "daily" / "2026-11-02.csv.gz").exists())    # 아직 열린 날

        fresh = at("2026-11-04", "00:30")                                        # 피드는 계속 새 기사를 낸다
        write_feed(env.fx, "a-eco", [("겨울 패딩 판매 호조", fresh)])
        write_feed(env.fx, "b-eco", [("김장 비용 부담 커져", fresh)])
        s, _ = env.collect("2026-11-04T01:00:00+09:00")                          # 이틀 뒤 → 확정
        gz = env.data / "daily" / "2026-11-02.csv.gz"
        self.assertTrue(gz.exists())
        self.assertFalse((env.data / "daily" / "2026-11-02.csv").exists())
        header, rows = read_gz_csv(gz)
        self.assertEqual(header, ["keyword", "n", "articles", "outlets"])        # 열 구성은 이전과 같다
        by = {r["keyword"]: r for r in rows}
        self.assertEqual(by["삼성전자"]["articles"], "1")                         # 하루 1건 키워드가 저장됨
        self.assertEqual(by["편의점"]["articles"], "2")                           # 2건 이상 키워드도 저장됨
        self.assertEqual(by["편의점"]["outlets"], "2")
        self.assertEqual(by["편의점"]["n"], "1")
        days = {d["date"]: d for d in quality.read_days(env.data / "days.csv")}
        self.assertIn("2026-11-02", days)
        self.assertEqual(sha(legacy), legacy_hash)                               # 기존 날짜 파일은 그대로

        # 확정된 날의 기사가 36시간 창에 남아 있는 상황 (실제 운영에서 파일을 훼손했던 경우)
        write_feed(env.fx, "a-eco", [("늦게 남은 기사 편의점 디저트", at("2026-11-02", "15:00")),
                                     ("겨울 패딩 판매 호조", fresh)])
        gz_hash, days_text = sha(gz), (env.data / "days.csv").read_text(encoding="utf-8")
        s, _ = env.collect("2026-11-04T01:30:00+09:00")
        self.assertGreaterEqual(s["closed_ignored"], 1)
        self.assertEqual(sha(gz), gz_hash)
        self.assertEqual((env.data / "days.csv").read_text(encoding="utf-8"), days_text)
        self.assertEqual(sha(legacy), legacy_hash)

        # 프로세스 사이에 저장된 상태에도 헤드라인 원문이 없다
        raw = gzip.open(env.state / "state.json.gz", "rt", encoding="utf-8").read()
        self.assertNotIn("디저트 신상 출시", raw)
        self.assertNotIn("양산 돌입", raw)

    def test_304_freshness_through_collect_with_fixtures(self):
        env = Env([feed("a-eco"), feed("b-eco")])
        write_feed(env.fx, "a-eco", [("편의점 디저트 신상 출시", at("2026-11-02", "09:00"))])
        write_feed(env.fx, "b-eco", [("삼성전자 HBM4 양산 돌입", at("2026-11-02", "09:00"))])
        s, _ = env.collect("2026-11-02T10:00:00+09:00")
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (2, 2))

        # a 는 이후 304 만 돌려준다. b 는 계속 새 기사를 낸다.
        (env.fx / "a-eco.not_modified").write_text("")
        write_feed(env.fx, "b-eco", [("전기차 보조금 확대 발표", at("2026-11-03", "07:30"))])
        s, _ = env.collect("2026-11-03T08:00:00+09:00")                          # 마지막 기사로부터 23시간
        self.assertEqual(s["statuses"].get("not_modified"), 1)
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (2, 2))
        self.assertEqual(s["failures"], [])

        write_feed(env.fx, "b-eco", [("전기차 보조금 확대 발표", at("2026-11-03", "11:30"))])
        s, _ = env.collect("2026-11-03T12:00:00+09:00")                          # 27시간 → 정지 의심
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (1, 2))
        self.assertEqual([f[:2] for f in s["failures"]], [["a-eco", "stale"]])
        # 최신 시각이 프로세스 사이에 저장돼 있다
        st = json.loads(gzip.open(env.state / "state.json.gz", "rt", encoding="utf-8").read())
        self.assertEqual(st["http"]["a-eco"]["newest_ts"], at("2026-11-02", "09:00"))

    def test_304_through_real_http_path_and_exit_code(self):
        body = rss([("편의점 디저트 신상 출시", at("2026-11-02", "09:00"))])

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/robots.txt":
                    out, code, extra = b"User-agent: *\nAllow: /\n", 200, {}
                elif self.path == "/feed.xml" and self.headers.get("If-None-Match") == '"v1"':
                    self.send_response(304)
                    self.end_headers()
                    return
                elif self.path == "/feed.xml":
                    out, code, extra = body, 200, {"ETag": '"v1"'}
                else:
                    out, code, extra = b"nope", 404, {}
                self.send_response(code)
                self.send_header("Content-Length", str(len(out)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(out)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        env = Env([feed("a-eco", f"http://127.0.0.1:{srv.server_address[1]}/feed.xml")])

        s, _ = env.collect("2026-11-02T10:00:00+09:00", fixtures=False)
        self.assertEqual(s["statuses"], {"ok": 1})
        s, _ = env.collect("2026-11-03T08:00:00+09:00", fixtures=False)           # 304, 마지막 기사로부터 23시간
        self.assertEqual(s["statuses"], {"not_modified": 1})
        self.assertEqual(s["feeds_ok"], 1)
        s, out = env.collect("2026-11-03T12:00:00+09:00", fixtures=False, expect_rc=1)   # 304, 27시간 → 정상 피드 0개
        self.assertEqual(s["statuses"], {"not_modified": 1})
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (0, 1))
        self.assertEqual([f[:2] for f in s["failures"]], [["a-eco", "stale"]])
        st = json.loads(gzip.open(env.state / "state.json.gz", "rt", encoding="utf-8").read())
        self.assertEqual(st["http"]["a-eco"]["etag"], '"v1"')                     # ETag 도 유지된다

    def test_newest_ts_survives_an_undated_200_then_304_goes_stale(self):
        """날짜 있는 200 → 날짜 없는 200(ETag 바뀜) → 304. 보존된 시각으로 정지 판정이 되어야 한다."""
        dated = rss([("편의점 디저트 신상 출시", at("2026-11-02", "09:00"))])
        undated = (b'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>'
                   b'<item><title>' + "삼성전자 HBM4 양산 돌입".encode() + b'</title><link>https://e.com/2</link></item>'
                   b'</channel></rss>')
        mode = {"body": dated, "etag": '"v1"'}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/robots.txt":
                    out, code, extra = b"User-agent: *\nAllow: /\n", 200, {}
                elif self.path == "/feed.xml" and self.headers.get("If-None-Match") == mode["etag"]:
                    self.send_response(304)
                    self.end_headers()
                    return
                elif self.path == "/feed.xml":
                    out, code, extra = mode["body"], 200, {"ETag": mode["etag"]}
                else:
                    out, code, extra = b"nope", 404, {}
                self.send_response(code)
                self.send_header("Content-Length", str(len(out)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(out)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        env = Env([feed("a-eco", f"http://127.0.0.1:{srv.server_address[1]}/feed.xml")])

        def state_http():
            return json.loads(gzip.open(env.state / "state.json.gz", "rt", encoding="utf-8").read())["http"]["a-eco"]

        # 1. 날짜가 있는 RSS → newest_ts 저장
        env.collect("2026-11-02T10:00:00+09:00", fixtures=False)
        self.assertEqual(state_http()["newest_ts"], at("2026-11-02", "09:00"))
        self.assertEqual(state_http()["etag"], '"v1"')

        # 2. 날짜 없는 RSS 를 200 으로 수집 (ETag 는 바뀜)
        mode.update(body=undated, etag='"v2"')
        s, _ = env.collect("2026-11-02T12:00:00+09:00", fixtures=False)
        self.assertEqual(s["statuses"], {"ok": 1})
        self.assertEqual(s["nodate"], 1)
        # 3. 기존 newest_ts 가 사라지지 않고, ETag 는 새 값으로 갱신된다
        self.assertEqual(state_http()["newest_ts"], at("2026-11-02", "09:00"))
        self.assertEqual(state_http()["etag"], '"v2"')

        # 4. 이후 304: 보존된 시각 기준으로 23시간은 정상, 27시간은 정지 의심
        s, _ = env.collect("2026-11-03T08:00:00+09:00", fixtures=False)
        self.assertEqual((s["statuses"], s["feeds_ok"]), ({"not_modified": 1}, 1))
        s, _ = env.collect("2026-11-03T12:00:00+09:00", fixtures=False, expect_rc=1)
        self.assertEqual((s["feeds_ok"], s["feeds_expected"]), (0, 1))
        self.assertEqual([f[:2] for f in s["failures"]], [["a-eco", "stale"]])


if __name__ == "__main__":
    unittest.main()
