"""공용 유틸: 시간대, 설정 읽기."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


def kst_day(ts: float) -> str:
    """epoch 초를 한국시간 날짜 문자열(YYYY-MM-DD)로."""
    return dt.datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")


def kst_shift(day: str, days: int) -> str:
    d = dt.datetime.strptime(day, "%Y-%m-%d") + dt.timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def load_settings(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_wordlist(path: str | Path) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out
