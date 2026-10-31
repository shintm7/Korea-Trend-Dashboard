#!/usr/bin/env python3
"""수집 현황과 급증 판정 가능 여부를 출력한다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.quality import baseline_status, read_days  # noqa: E402
from radar.util import load_settings  # noqa: E402


def main() -> int:
    cfg = load_settings()
    days = ROOT / "data" / "days.csv"
    base = baseline_status(days, cfg)
    print(f"기록된 날 {base['days_recorded']}일, 완전한 날 {base['complete_days']}/{base['required']}일")
    print("급증 판정:", "가능" if base["ready"] else "보류 (완전한 날이 부족합니다)")
    for r in read_days(days)[-14:]:
        mark = "불완전 " + r["flags"] if str(r["incomplete"]) == "1" else "완전"
        print(f"  {r['date']}  기사 {r['stories']:>5}  키워드 {r['keywords_stored']:>5}  "
              f"수집률 {r['coverage']}  {mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
