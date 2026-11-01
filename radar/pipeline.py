"""수집 한 회차(run_once)와 하루 확정(finalize_days).

흐름
  1. 모든 활성 피드를 가져온다 (robots.txt 준수, 서버별 간격, 조건부 요청)
  2. 새 기사만 해시로 골라 키워드를 뽑고 상태(state)에 쌓는다. 헤드라인 원문은 쌓지 않는다.
  3. 날이 바뀌어 더 이상 기사가 늘지 않는 날은 확정해서 data/ 에 파일로 쓴다.
     (열려 있는 오늘·어제는 상태에만 두고, 상태에서 매번 다시 계산하므로 중복 집계가 없다)
"""
from __future__ import annotations

import collections
import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import alias, quality, store
from .extract import Rules, extract, normalize_title, title_hash
from .feeds import Feed, FetchResult
from .util import kst_day, kst_shift


def _day_stats(state: dict, day: str) -> dict:
    return state["stats"].setdefault(
        day, {"runs": 0, "expected_sum": 0.0, "ok_sum": 0.0, "stale_runs": 0, "feeds": {}, "reset": False})


def run_once(cfg: dict, feeds: list[Feed], fetcher, state: dict, now: float, rules: Rules,
             reset: bool = False, log=print) -> dict:
    today = kst_day(now)
    st = _day_stats(state, today)
    if reset:
        st["reset"] = True

    enabled = [f for f in feeds if f.enabled]
    feed_by_id = {f.id: f for f in enabled}
    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as ex:
        results: list[FetchResult] = list(ex.map(lambda f: fetcher.fetch(f, now=now), enabled))

    status_count = collections.Counter()
    counts = collections.Counter()
    blocked = ok_fresh = stale_n = 0
    failures = []
    max_age = cfg["max_item_age_hours"] * 3600
    open_days = {kst_shift(today, -i) for i in range(cfg["state_keep_days"])}   # 아직 확정하지 않은 날
    stories = state["stories"]

    for res in results:
        feed = feed_by_id[res.feed_id]
        status_count[res.status] += 1
        fs = st["feeds"].setdefault(res.feed_id, {"runs": 0, "ok": 0, "stale": 0, "items_new": 0,
                                                  "last_status": "", "last_error": ""})
        # 304(변경 없음)는 새 기사가 없다는 뜻일 뿐이라, 마지막으로 본 최신 기사 시각으로 정지 여부를 따로 판정한다.
        # 저장된 시각이 없으면(상태 유실·날짜 없는 피드) 판단할 근거가 없어 정지로 보지 않는다.
        if res.status == "not_modified":
            last = state["http"].get(res.feed_id, {}).get("newest_ts")
            if last is not None:
                res.newest_ts = last
                res.stale = (now - last) > cfg["stale_feed_hours"] * 3600
        fs["runs"] += 1
        fs["last_status"], fs["last_error"] = res.status, res.error
        if res.status == "robots_blocked":
            blocked += 1
        if res.ok and not res.stale:
            fs["ok"] += 1
            ok_fresh += 1
        if res.stale:
            fs["stale"] += 1
            stale_n += 1
        if not res.ok or res.stale:
            failures.append((res.feed_id, "stale" if res.stale and res.ok else res.status, res.error))
        if res.status == "ok" and (res.etag or res.last_modified or res.newest_ts is not None):
            # 조건부 요청용 ETag/Last-Modified 와 함께, 304 때 쓸 최신 기사 시각을 보관한다
            state["http"][res.feed_id] = {"etag": res.etag, "last_modified": res.last_modified,
                                          "newest_ts": res.newest_ts}

        for it in res.items:
            ts = it.ts
            h = title_hash(normalize_title(it.title))
            undated = ts is None
            if undated:
                counts["nodate"] += 1
                ts = now
            elif ts > now + 7200:
                ts = now                      # 미래 시각은 지금으로 보정
            if now - ts > max_age:
                counts["old"] += 1
                continue
            day = kst_day(ts)
            if day not in open_days:
                # 이미 확정(파일로 기록)된 날의 기사가 36시간 창 안에 남아 다시 들어오면, 확정된 파일을 일부 기사만으로 덮어쓴다.
                counts["closed"] += 1
                continue
            key = f"{day}:{h}"
            if undated:
                # 날짜 없는 피드는 같은 기사가 며칠째 목록에 남는다. 처음 본 해시를 7일간 기억해 다시 세지 않는다.
                seen = state.setdefault("undated_seen", {})
                if h in seen and key not in stories:
                    counts["dup"] += 1
                    continue
                seen.setdefault(h, now)
            story = stories.get(key)
            if story is not None:
                if feed.pub not in story["pubs"]:
                    story["pubs"].append(feed.pub)
                    story["pubs"].sort()
                counts["dup"] += 1
                continue
            ex_ = extract(it.title, rules)
            stories[key] = {"day": day, "pubs": [feed.pub], "kw": ex_.keywords, "p": 1 if ex_.political else 0}
            fs["items_new"] += 1
            counts["politics" if ex_.political else "new"] += 1

    seen = state.get("undated_seen")
    if seen:
        state["undated_seen"] = {h: t for h, t in seen.items() if now - t <= 7 * 86400}

    expected = len(enabled) - blocked
    st["runs"] += 1
    st["expected_sum"] += expected
    st["ok_sum"] += ok_fresh
    st["stale_runs"] += stale_n
    state["meta"]["last_run"] = now

    summary = {
        "day": today, "feeds_enabled": len(enabled), "feeds_expected": expected, "feeds_ok": ok_fresh,
        "statuses": dict(status_count), "new_stories": counts["new"], "politics_dropped": counts["politics"],
        "duplicates": counts["dup"], "old_ignored": counts["old"], "closed_ignored": counts["closed"],
        "nodate": counts["nodate"],
        "failures": failures,
    }
    log(f"[run {today}] feeds ok {ok_fresh}/{expected} | status {dict(status_count)} | "
        f"new {counts['new']} politics_dropped {counts['politics']} dup {counts['dup']} old {counts['old']} "
        f"closed {counts['closed']}")
    for fid, kind, err in failures:
        log(f"   ! {fid}: {kind} {err}".rstrip())
    return summary


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def aggregate(stories: list[dict], registry: dict, cfg: dict) -> tuple[list[dict], int, list[dict]]:
    mapping, logs = alias.canonicalize([s["kw"] for s in stories], registry)
    arts: collections.Counter = collections.Counter()
    pubs: dict[str, set] = collections.defaultdict(set)
    for s in stories:
        for k in {mapping.get(k, k) for k in s["kw"]}:
            arts[k] += 1
            pubs[k].update(s["pubs"])
    rows, below = [], 0
    for k, c in arts.items():
        if c >= cfg["min_articles_to_store"]:   # 기본 1: 하루 1건 키워드도 장기 보관한다
            rows.append({"keyword": k, "n": k.count(" ") + 1, "articles": c, "outlets": len(pubs[k])})
        else:
            below += 1
    rows.sort(key=lambda r: (-r["articles"], -r["outlets"], r["keyword"]))
    return rows, below, logs


def write_day(day: str, state: dict, cfg: dict, data_dir: Path, log=print) -> list[str]:
    stories = [s for s in state["stories"].values() if s["day"] == day]
    news = [s for s in stories if not s["p"]]
    stats = state["stats"].get(day)

    reg_path = data_dir / "aliases" / "canon.json"
    map_path = data_dir / "aliases" / "alias_map.json"
    log_path = data_dir / "aliases" / "merge_log.csv"
    registry = alias.load_json(reg_path)
    alias_map = alias.load_json(map_path)

    rows, below, merge_logs = aggregate(news, registry, cfg)
    new_initials = alias.detect_initials(rows, alias_map, cfg)
    for e in new_initials:
        alias_map[e["alias"]] = e["canonical"]

    written = []
    gz = cfg.get("daily_gzip", True)
    store.write_daily(data_dir, day, rows, gz=gz)
    written.append(f"daily/{day}.csv.gz" if gz else f"daily/{day}.csv")
    if merge_logs or new_initials:
        alias.append_log(log_path, day, merge_logs + new_initials)
    alias.save_json(reg_path, registry)
    alias.save_json(map_path, alias_map)

    if stats and stats["runs"] > 0:
        coverage = round(stats["ok_sum"] / stats["expected_sum"], 3) if stats["expected_sum"] else 0.0
        expected_feeds = round(stats["expected_sum"] / stats["runs"])
        feed_rows = [{"feed_id": fid, "runs": v["runs"], "ok_runs": v["ok"], "stale_runs": v["stale"],
                      "items_new": v["items_new"], "last_status": v["last_status"], "last_error": v["last_error"]}
                     for fid, v in sorted(stats["feeds"].items())]
        _write_csv(data_dir / "health" / f"{day}.csv",
                   ["feed_id", "runs", "ok_runs", "stale_runs", "items_new", "last_status", "last_error"], feed_rows)
        written.append(f"health/{day}.csv")
        runs, reset, stale_runs = stats["runs"], stats["reset"], stats["stale_runs"]
    else:
        coverage, expected_feeds, runs, reset, stale_runs = 0.0, 0, 0, False, 0

    meta = {"date": day, "stories": len(news), "politics_dropped": len(stories) - len(news),
            "keywords_stored": len(rows), "keywords_below_min": below, "runs": runs,
            "expected_feeds": expected_feeds, "coverage": coverage, "stale_feed_runs": stale_runs, "reset": reset}
    days_csv = data_dir / "days.csv"
    flags = quality.evaluate_day(meta, quality.read_days(days_csv), cfg)
    meta["incomplete"] = 1 if flags else 0
    meta["flags"] = "|".join(flags)
    quality.upsert_day(days_csv, meta)
    written.append("days.csv")
    log(f"[finalize {day}] stories {meta['stories']} keywords {len(rows)} coverage {coverage} "
        f"runs {runs} incomplete={meta['incomplete']} {meta['flags']}".rstrip())
    return written


def finalize_days(cfg: dict, state: dict, now: float, data_dir: Path, force_all: bool = False,
                  log=print) -> list[str]:
    """열려 있는 날(오늘·어제)을 제외하고 확정한다. 확정한 날의 상태는 지운다."""
    today = kst_day(now)
    keep = {kst_shift(today, -i) for i in range(cfg["state_keep_days"])}
    days = {s["day"] for s in state["stories"].values()} | set(state["stats"].keys())
    written: list[str] = []
    recorded = {r["date"] for r in quality.read_days(data_dir / "days.csv")}
    for day in sorted(days):
        if day in keep and not force_all:
            continue
        if day in recorded and day not in state["stats"] and not force_all:
            # 이미 확정된 날의 낙오 기사만 남은 경우: 확정 파일을 덮어쓰지 않고 버린다
            state["stories"] = {k: v for k, v in state["stories"].items() if v["day"] != day}
            log(f"[finalize {day}] 이미 확정된 날이라 상태에 남은 기사를 버립니다 (파일 유지)")
            continue
        written += write_day(day, state, cfg, data_dir, log=log)
        state["stories"] = {k: v for k, v in state["stories"].items() if v["day"] != day}
        state["stats"].pop(day, None)
    return written
