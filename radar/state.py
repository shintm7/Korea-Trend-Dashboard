"""수집 상태 파일. git에는 올리지 않고 Actions 캐시에만 둔다 (state/ 폴더).

stories : "<날짜>:<제목해시>" -> {day, pubs, kw, p}   헤드라인 원문 없이 해시·키워드·매체만 보관
http    : 피드별 ETag / Last-Modified (조건부 요청용)
stats   : 날짜별 수집 건강 기록 (회차 수, 정상 피드 수, 피드별 결과)
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

VERSION = 1


def new_state() -> dict:
    return {"version": VERSION, "stories": {}, "http": {}, "stats": {}, "meta": {}}


def load_state(path: str | Path) -> tuple[dict, bool]:
    """(상태, 파일이 없거나 깨졌는지)"""
    path = Path(path)
    if not path.exists():
        return new_state(), True
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            st = json.load(f)
        if st.get("version") != VERSION:
            return new_state(), True
        return st, False
    except (OSError, ValueError, EOFError):
        return new_state(), True


def save_state(path: str | Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
