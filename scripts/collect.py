#!/usr/bin/env python3
"""뉴스 수집 한 회차를 실행한다.

  python scripts/collect.py                         # 실제 수집 (GitHub Actions에서 2시간마다)
  python scripts/collect.py --fixtures tests/fixtures/set1 --now 2026-11-02T10:00:00+09:00   # 오프라인 시뮬레이션
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.extract import Rules  # noqa: E402
from radar.feeds import FixtureFetcher, HttpFetcher, load_feeds  # noqa: E402
from radar.pipeline import finalize_days, run_once  # noqa: E402
from radar.quality import baseline_status, read_days  # noqa: E402
from radar.state import load_state, save_state  # noqa: E402
from radar.util import load_settings, read_wordlist  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    ap.add_argument("--feeds", default=str(ROOT / "config" / "feeds.yaml"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--state-dir", default=str(ROOT / "state"))
    ap.add_argument("--fixtures", help="네트워크 대신 이 폴더의 XML을 사용 (테스트용)")
    ap.add_argument("--now", help="현재 시각을 고정 (ISO 8601, 시뮬레이션용)")
    ap.add_argument("--finalize-all", action="store_true", help="열린 날까지 모두 확정해서 쓴다")
    ap.add_argument("--include-disabled", action="store_true", help="enabled:false 피드도 포함 (테스트용)")
    args = ap.parse_args()

    cfg = load_settings(args.config)
    data_dir, state_path = Path(args.data_dir), Path(args.state_dir) / "state.json.gz"
    now = dt.datetime.fromisoformat(args.now).timestamp() if args.now else time.time()

    rules = Rules.from_lists(read_wordlist(ROOT / "config" / "stopwords.txt"),
                             read_wordlist(ROOT / "config" / "politics_terms.txt"),
                             cfg["ngram_max"], cfg["max_keywords_per_headline"])
    feeds = load_feeds(args.feeds)
    if args.include_disabled:
        for f in feeds:
            f.enabled = True

    state, missing = load_state(state_path)
    had_history = bool(read_days(data_dir / "days.csv"))
    reset = missing and had_history        # 첫 실행은 장애가 아니다. 기록이 있는데 상태만 없으면 상태 유실.
    if reset:
        print("! 상태 파일이 없어 새로 시작합니다. 오늘은 '불완전한 날'로 표시됩니다.")

    fetcher = FixtureFetcher(args.fixtures, cfg) if args.fixtures else HttpFetcher(cfg, state["http"])
    summary = run_once(cfg, feeds, fetcher, state, now, rules, reset=reset)
    written = finalize_days(cfg, state, now, data_dir, force_all=args.finalize_all)
    save_state(state_path, state)

    base = baseline_status(data_dir / "days.csv", cfg)
    print(f"[baseline] 완전한 날 {base['complete_days']}/{base['required']} → "
          f"{'급증 판정 가능' if base['ready'] else '급증 판정 보류 (기준선 쌓는 중)'}")
    if written:
        print("[written] " + ", ".join(sorted(set(written))))

    out = Path(args.state_dir) / "last_run.json"
    out.write_text(json.dumps({**summary, "written": written, "baseline": base}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    # 정상 수집한 피드가 하나도 없으면 실패로 끝내 Actions에서 바로 보이게 한다
    return 1 if summary["feeds_expected"] and summary["feeds_ok"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
