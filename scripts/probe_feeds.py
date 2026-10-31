#!/usr/bin/env python3
"""모든 피드(비활성 포함)의 접근 가능 여부를 점검해 표로 보고한다.

GitHub Actions 서버에서 돌려야 실제 수집 환경과 같은 결과가 나온다 (일부 언론사는 서버 IP를 막는다).
  python scripts/probe_feeds.py            # reports/probe-latest.md 와 .json 생성
"""
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.feeds import HttpFetcher, load_feeds  # noqa: E402
from radar.util import KST, load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", default=str(ROOT / "config" / "feeds.yaml"))
    ap.add_argument("--out-dir", default=str(ROOT / "reports"))
    args = ap.parse_args()

    cfg = load_settings()
    feeds = load_feeds(args.feeds)
    fetcher = HttpFetcher(cfg)
    now = time.time()
    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        results = list(ex.map(lambda f: fetcher.fetch(f, now=now, use_conditional=False), feeds))

    rows = []
    for f, r in zip(feeds, results):
        age = round((now - r.newest_ts) / 3600, 1) if r.newest_ts else None
        verdict = ("정상" if r.ok and not r.stale else "정지 의심" if r.ok else "실패")
        rows.append({"id": f.id, "name": f.name, "section": f.section, "enabled": f.enabled,
                     "status": r.status, "http": r.http, "items": len(r.items), "newest_age_h": age,
                     "robots": r.robots, "verdict": verdict, "error": r.error, "url": f.url})

    stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    good = [r for r in rows if r["verdict"] == "정상"]
    lines = [f"# RSS 피드 점검 ({stamp})", "",
             f"- 전체 {len(rows)}개 중 정상 {len(good)}개, 정지 의심 {sum(r['verdict']=='정지 의심' for r in rows)}개, "
             f"실패 {sum(r['verdict']=='실패' for r in rows)}개",
             f"- 활성(enabled) 피드 중 정상 {sum(r['verdict']=='정상' and r['enabled'] for r in rows)}/"
             f"{sum(r['enabled'] for r in rows)}", "",
             "| 판정 | id | 활성 | 상태 | HTTP | 항목 | 최신기사(시간 전) | robots | 오류 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["verdict"] != "실패", r["verdict"] != "정지 의심", r["id"])):
        lines.append(f"| {r['verdict']} | {r['id']} | {'O' if r['enabled'] else '-'} | {r['status']} | "
                     f"{r['http'] or ''} | {r['items']} | {r['newest_age_h'] if r['newest_age_h'] is not None else ''} | "
                     f"{r['robots']} | {r['error'][:60]} |")
    md = "\n".join(lines) + "\n"

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probe-latest.md").write_text(md, encoding="utf-8")
    (out / "probe-latest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
