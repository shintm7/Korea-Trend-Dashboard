#!/usr/bin/env python3
"""네이버 데이터랩 검색어트렌드 API로 소비·산업 트렌드를 수집해 docs/data.json 으로 저장.

사용법
  NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... python scripts/collect.py
  python scripts/collect.py --demo      # API 키 없이 가짜 데이터로 화면 확인용

주의: 네이버 데이터랩의 ratio는 '한 번의 요청(=한 섹션) 안에서' 가장 높은 값을 100으로
정규화한 상대값입니다. 따라서 섹션이 다른 그룹끼리는 값을 직접 비교할 수 없고,
증감률(최근 7일 vs 직전 7일 등)은 같은 그룹 안에서만 의미가 있습니다.
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
OUT = ROOT / "docs" / "data.json"
API_URL = "https://openapi.naver.com/v1/datalab/search"
KST = dt.timezone(dt.timedelta(hours=9))


def fetch_section(section, start, end, cid, secret):
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "timeUnit": "date",
        "keywordGroups": [
            {"groupName": g["name"], "keywords": g["keywords"]} for g in section["groups"]
        ],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "X-Naver-Client-Id": cid,
            "X-Naver-Client-Secret": secret,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        payload = json.load(res)
    return {r["title"]: r["data"] for r in payload["results"]}


def demo_series(start, end, seed):
    rnd = random.Random(seed)
    days = (end - start).days + 1
    base = rnd.uniform(20, 60)
    slope = rnd.uniform(-0.3, 0.6)
    amp = rnd.uniform(3, 10)
    out = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        v = base + slope * i + amp * math.sin(i / 3.0) + rnd.uniform(-4, 4)
        out.append({"period": d.isoformat(), "ratio": round(max(v, 0.5), 5)})
    peak = max(p["ratio"] for p in out) or 1
    for p in out:
        p["ratio"] = round(p["ratio"] / peak * 100, 5)
    return out


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def pct(new, old):
    if old <= 0:
        return None
    return round((new - old) / old * 100, 1)


def analyze(series):
    vals = [p["ratio"] for p in series]
    last7 = avg(vals[-7:])
    prev7 = avg(vals[-14:-7])
    prev28 = avg(vals[-35:-7])
    return {
        "last7": round(last7, 2),
        "wow_pct": pct(last7, prev7),        # 직전 7일 대비
        "momentum_pct": pct(last7, prev28),  # 직전 4주 평균 대비 (추세 가속 여부)
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    today = dt.datetime.now(KST).date()
    end = today - dt.timedelta(days=1)  # 당일 데이터는 미확정이라 어제까지
    start = end - dt.timedelta(days=cfg.get("days", 90) - 1)

    cid = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not args.demo and not (cid and secret):
        sys.exit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다. (--demo로 샘플 실행 가능)")

    sections = []
    for si, sec in enumerate(cfg["sections"]):
        if args.demo:
            raw = {g["name"]: demo_series(start, end, seed=si * 100 + gi)
                   for gi, g in enumerate(sec["groups"])}
        else:
            raw = fetch_section(sec, start, end, cid, secret)
        groups = []
        for g in sec["groups"]:
            series = raw.get(g["name"], [])
            groups.append({
                "name": g["name"],
                "keywords": g["keywords"],
                "series": series,
                **analyze(series),
            })
        sections.append({"id": sec["id"], "title": sec["title"], "groups": groups})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
                "range": [start.isoformat(), end.isoformat()],
                "demo": bool(args.demo),
                "source": "네이버 데이터랩 검색어트렌드",
                "sections": sections,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"saved {OUT} ({sum(len(s['groups']) for s in sections)} groups)")


if __name__ == "__main__":
    main()
