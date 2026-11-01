"""일일 집계 파일(data/daily) 읽기·쓰기와 최초 등장일 조회.

일일 파일은 하루 기사 수가 1건인 키워드까지 모두 담는다 (min_articles_to_store: 1).
헤드라인 원문을 보관하지 않으므로 한 번 버린 1건 키워드는 되살릴 수 없기 때문이다.
화면·분석에 쓰는 기준(min_articles_to_display)은 저장 기준과 분리해 읽을 때 적용한다.

파일 형식: 기본 data/daily/날짜.csv.gz (gzip, mtime 고정). 예전 형식의 날짜.csv 도 그대로 읽는다.
열은 keyword,n,articles,outlets 로 이전과 같다.
"""
from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path

FIELDS = ["keyword", "n", "articles", "outlets"]


def daily_path(data_dir: str | Path, day: str, gz: bool = True) -> Path:
    return Path(data_dir) / "daily" / (f"{day}.csv.gz" if gz else f"{day}.csv")


def write_daily(data_dir: str | Path, day: str, rows: list[dict], gz: bool = True) -> Path:
    """일일 집계를 쓴다. 같은 날의 다른 형식 파일이 있으면 지워 두 벌이 남지 않게 한다."""
    path = daily_path(data_dir, day, gz)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    if gz:
        with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            f.write(data)
    else:
        path.write_bytes(data)
    other = daily_path(data_dir, day, not gz)
    if other.exists():
        other.unlink()
    return path


def find_daily(data_dir: str | Path, day: str) -> Path | None:
    for gz in (True, False):
        p = daily_path(data_dir, day, gz)
        if p.exists():
            return p
    return None


def read_daily(data_dir: str | Path, day: str, min_articles: int = 1) -> list[dict]:
    """하루치 집계를 읽는다. min_articles 로 화면 표시 기준을 적용한다 (저장 기준과 별개)."""
    p = find_daily(data_dir, day)
    if p is None:
        return []
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", encoding="utf-8", newline="") as f:
        rows = [{"keyword": r["keyword"], "n": int(r["n"]), "articles": int(r["articles"]),
                 "outlets": int(r["outlets"])} for r in csv.DictReader(f)]
    return [r for r in rows if r["articles"] >= min_articles]


def list_days(data_dir: str | Path) -> list[str]:
    d = Path(data_dir) / "daily"
    if not d.exists():
        return []
    days = {p.name.split(".")[0] for p in d.iterdir() if p.name.endswith((".csv", ".csv.gz"))}
    return sorted(days)


def first_seen(data_dir: str | Path) -> dict[str, str]:
    """키워드별 최초 등장일. 일일 파일을 날짜순으로 훑어 처음 나온 날을 돌려준다.

    일일 파일에 이미 모든 키워드가 들어 있어 별도 색인 파일을 두지 않는다 (같은 내용을 두 번 저장하지 않으려고).
    최초 등장일은 기록이 시작된 날 이후로만 믿을 수 있다. 시작일 당일에 나온 키워드는 '그 전부터 있던 것'일 수 있다.
    """
    seen: dict[str, str] = {}
    for day in list_days(data_dir):
        for r in read_daily(data_dir, day):
            seen.setdefault(r["keyword"], day)
    return seen
