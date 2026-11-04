"""표시 계층 미리보기 (읽기 전용). 사용: python scripts/preview.py --day 2026-11-01 --top 40"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import display  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-articles", type=int, default=2)
    args = ap.parse_args()
    items = display.day_view(args.data_dir, args.day, min_articles=args.min_articles)
    print(f"{args.day}: 표시 항목 {len(items)}개 (규칙 버전 {display.load_config()['version']})")
    for it in items[: args.top]:
        extra = ""
        if len(it["members"]) > 1:
            extra = "  ← " + ", ".join(f"{m['keyword']}({m['articles']})" for m in it["members"])
        flag = f" [{','.join(it['flags'])}]" if it["flags"] else ""
        print(f"{it['score']:6.1f}  {it['label']}  ({it['articles']}건/{it['outlets']}곳){flag}{extra}")


if __name__ == "__main__":
    main()
