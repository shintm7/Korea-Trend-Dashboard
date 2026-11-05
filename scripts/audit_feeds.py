#!/usr/bin/env python3
"""RSS 출처 감사 (읽기 전용, 수동 실행).

현재 활성 피드와 공개 목록 후보를 같은 방식으로 요청해 상태·응답 시간·최신 기사 시각·기사 수를 재고,
기사 중복률과 신규 기여도를 계산해 보고서를 만든다.

- feeds.yaml, settings.yaml, data/ 는 읽기만 한다. 신규 후보를 수집 대상에 넣지 않는다.
- 기존 수집과 같은 정책을 쓴다: robots.txt 확인, 서버당 최소 요청 간격(host_min_interval_sec), 타임아웃, 재시도.
- 헤드라인 원문은 메모리에서만 쓰고 출력 파일에 넣지 않는다.

  python scripts/audit_feeds.py --out-dir audit-out
  python scripts/audit_feeds.py --fixtures tests/fixtures/audit --now 2026-11-05T12:00:00+09:00 --out-dir /tmp/x   # 오프라인 시험
"""
import argparse
import csv
import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import audit  # noqa: E402
from radar.extract import Rules  # noqa: E402
from radar.feeds import FixtureFetcher, HttpFetcher, load_feeds  # noqa: E402
from radar.util import KST, load_settings, read_wordlist  # noqa: E402


def install_timing(fetcher: HttpFetcher) -> dict:
    """요청별 응답 시간(밀리초)을 URL 별로 기록한다. 네트워크 동작은 바꾸지 않는다."""
    times: dict[str, int] = {}
    orig = fetcher.session.get

    def get(url, **kw):
        t0 = time.time()
        try:
            return orig(url, **kw)
        finally:
            times[url] = round((time.time() - t0) * 1000)

    fetcher.session.get = get
    return times


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=str(ROOT / "config" / "settings.yaml"))
    ap.add_argument("--feeds", default=str(ROOT / "config" / "feeds.yaml"))
    ap.add_argument("--candidates", default=str(ROOT / "config" / "feeds_candidates.csv"))
    ap.add_argument("--audit-config", default=str(ROOT / "config" / "audit.yaml"))
    ap.add_argument("--out-dir", default=str(ROOT / "audit-out"))
    ap.add_argument("--fixtures", help="네트워크 없이 로컬 파일로 시험 (테스트용)")
    ap.add_argument("--now", help="기준 시각(ISO). 시험용")
    args = ap.parse_args()

    settings = load_settings(args.settings)
    acfg = audit.load_config(args.audit_config)
    rules = Rules.from_lists(read_wordlist(ROOT / "config/stopwords.txt"), read_wordlist(ROOT / "config/politics_terms.txt"),
                             settings.get("ngram_max", 3), settings.get("max_keywords", 80))
    current = [f for f in load_feeds(args.feeds) if f.enabled]
    cands = audit.load_candidates(args.candidates, acfg)

    timing = None
    if args.fixtures:
        fetcher = FixtureFetcher(args.fixtures, settings)
    else:
        fetcher = HttpFetcher(settings)
        timing = install_timing(fetcher)
    now = dt.datetime.fromisoformat(args.now).timestamp() if args.now else time.time()

    print(f"[audit] 현재 {len(current)}개 + 후보 {len(cands)}개 측정 시작 (서버당 {settings['host_min_interval_sec']}초 간격, "
          f"타임아웃 {settings['timeout_sec']}초, 동시 {settings['max_workers']}개)")
    result = audit.run_audit(settings, acfg, current, cands, fetcher, rules, now, timing)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = audit.feed_rows(result)
    with open(out / "audit_feeds.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=audit.FEED_COLS)
        w.writeheader()
        w.writerows(rows)
    with open(out / "audit_distribution.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["axis", "name", "items_before", "items_after_added_new_only"])
        for axis, b, a in (("publisher", "pub_before", "pub_after"), ("category", "cat_before", "cat_after")):
            for k in sorted(set(result["dist"][b]) | set(result["dist"][a])):
                w.writerow([axis, k, result["dist"][b].get(k, 0), result["dist"][a].get(k, 0)])
    stamp = dt.datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d %H:%M KST")
    md = audit.render_report(result, acfg, stamp)
    (out / "audit_report.md").write_text(md, encoding="utf-8")

    cur = [m for m in result["measures"] if m.kind == "current"]
    can = [m for m in result["measures"] if m.kind == "candidate"]
    print(f"[audit] 현재 정상 {sum(m.verdict == '정상' for m in cur)}/{len(cur)} | 후보 추가 검토 "
          f"{sum(m.verdict == '추가 검토' for m in can)}, 보류 {sum(m.verdict == '보류' for m in can)}, "
          f"제외 {sum(m.verdict == '제외' for m in can)} -> {out}")
    return 1 if cur and not any(m.verdict == "정상" for m in cur) else 0


if __name__ == "__main__":
    raise SystemExit(main())
