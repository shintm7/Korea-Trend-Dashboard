"""날짜별 수집 품질 기록(days.csv)과 '완전한 날' 판정.

수집 장애(피드 다운, 실행 누락)를 트렌드 하락으로 오인하지 않도록, 품질이 낮은 날은
incomplete=1 로 표시하고 기준선·급증 계산에서 제외한다.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

DAY_FIELDS = ["date", "stories", "politics_dropped", "keywords_stored", "keywords_below_min",
              "runs", "expected_feeds", "coverage", "stale_feed_runs", "incomplete", "flags"]


def read_days(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_days(path: str | Path, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["date"])
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DAY_FIELDS)
        w.writeheader()
        w.writerows(rows)


def upsert_day(path: str | Path, row: dict) -> None:
    rows = [r for r in read_days(path) if r["date"] != row["date"]]
    rows.append({k: row.get(k, "") for k in DAY_FIELDS})
    write_days(path, rows)


def evaluate_day(meta: dict, history: list[dict], cfg: dict) -> list[str]:
    """불완전한 날이면 이유(flags) 목록을 돌려준다. 빈 목록이면 완전한 날."""
    flags = []
    if meta.get("reset"):
        flags.append("state_reset")
    if meta["runs"] < cfg["min_runs_per_day"]:
        flags.append("runs_low")
    if meta["coverage"] < cfg["coverage_min"]:
        flags.append("coverage_low")
    recent = [int(r["stories"]) for r in history if r["incomplete"] in ("0", 0, "")
              and r["date"] < meta["date"]][-7:]
    if len(recent) >= 3:
        med = statistics.median(recent)
        if med > 0 and meta["stories"] < cfg["volume_drop_ratio"] * med:
            flags.append("volume_low")
    return flags


def baseline_status(days_csv: str | Path, cfg: dict) -> dict:
    rows = read_days(days_csv)
    complete = [r for r in rows if str(r["incomplete"]) in ("0", "")]
    need = cfg["baseline_days_required"]
    return {
        "days_recorded": len(rows),
        "complete_days": len(complete),
        "required": need,
        "ready": len(complete) >= need,
        "first_complete_day": complete[0]["date"] if complete else None,
        "last_day": rows[-1]["date"] if rows else None,
    }
