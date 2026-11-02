"""날짜별 수집 품질 기록(days.csv)과 '완전한 날' 판정.

수집 장애(피드 다운, 실행 누락)를 트렌드 하락으로 오인하지 않도록, 품질이 낮은 날은
incomplete=1 로 표시하고 기준선·급증 계산에서 제외한다.
"""
from __future__ import annotations

import csv
import datetime as dt
import statistics
from pathlib import Path

DAY_FIELDS = ["date", "stories", "politics_dropped", "keywords_stored", "keywords_below_min",
              "runs", "expected_feeds", "coverage", "stale_feed_runs", "max_gap_h", "incomplete", "flags"]


_KST = dt.timezone(dt.timedelta(hours=9))


def day_start_ts(day: str) -> float:
    return dt.datetime.fromisoformat(f"{day}T00:00:00+09:00").timestamp()


def max_gap_hours(run_ts: list, day: str) -> float:
    """그날에 걸친 가장 긴 수집 공백(시간). 하루 실행 횟수가 충분해도 한 번에 길게 비면 그 사이 RSS 에서 밀려난 기사를 놓친다.

    직전 날의 마지막 실행부터 다음 날의 첫 실행까지 이어서 본다 (자정을 넘는 공백도 센다).
    앞쪽에 실행 기록이 없으면 그날 0시를 시작으로, 뒤쪽에 없으면 24시를 끝으로 본다 (기록 없음 = 수집하지 못한 시간).
    """
    start = day_start_ts(day)
    end = start + 86400
    ts = sorted(set(float(t) for t in run_ts))
    before = [t for t in ts if t < start]
    inside = [t for t in ts if start <= t < end]
    after = [t for t in ts if t >= end]
    seq = [max(before) if before else start] + inside + [min(after) if after else end]
    return max(b - a for a, b in zip(seq, seq[1:])) / 3600


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
    if "max_gap_h" in meta and meta.get("runs", 0) > 0:
        gap = meta["max_gap_h"]
        if gap == "":
            flags.append("gap_unknown")          # 공백 기록이 시작되기 전의 날: 공백이 길었는지 알 수 없어 기준선에서 뺀다
        elif float(gap) > cfg.get("max_gap_hours", 6):
            flags.append("gap_long")
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
