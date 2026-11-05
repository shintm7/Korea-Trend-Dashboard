"""RSS 출처 감사: 피드 품질 측정, 기사 중복률, 신규 기여도 순위, 분포 분석.

읽기 전용 도구다. 수집 파이프라인(pipeline/store/quality)은 이 모듈을 가져오지 않고, 이 모듈은 데이터나 설정을 쓰지 않는다.
헤드라인 원문은 메모리 안에서 키워드·해시를 만드는 데만 쓰고 어떤 출력에도 넣지 않는다.
"""
from __future__ import annotations

import csv
import dataclasses
import re
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .extract import Rules, extract
from .feeds import Feed, FetchResult
from .util import ROOT

DEFAULT_CFG = ROOT / "config" / "audit.yaml"
_HANGUL = re.compile(r"[가-힣]")
_LETTER = re.compile(r"[A-Za-z가-힣]")


def load_config(path: str | Path | None = None) -> dict:
    with open(Path(path) if path else DEFAULT_CFG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_candidates(path: str | Path, cfg: dict) -> list[Feed]:
    """feeds_candidates.csv 에서 측정할 후보를 Feed 로 만든다. 수집 설정(feeds.yaml)과는 무관하다."""
    want = set(cfg["candidate_statuses"])
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), 1):
            if r["status_vs_current"] not in want:
                continue
            out.append(Feed(id=f"cand{i:03d}", pub=r["publisher"], name=r["publisher"], section=r["group"],
                            title=r["publisher"], url=r["url"], enabled=True, note=r.get("scope", "")))
    return out


def category_of(feed: Feed, kind: str, cfg: dict) -> str:
    cats = cfg["categories"]
    if kind == "candidate":
        return cats["candidate_group"].get(feed.section, feed.section)
    first = (feed.section or "").split("|")[0]
    return cats["current_section"].get(first, first or "기타")


def hangul_ratio(titles: list[str]) -> float:
    text = "".join(titles)
    letters = len(_LETTER.findall(text))
    return round(len(_HANGUL.findall(text)) / letters, 3) if letters else 0.0


@dataclasses.dataclass
class AItem:
    feed_id: str
    pub: str
    hash: str
    tokens: frozenset
    political: bool


def build_items(feed: Feed, res: FetchResult, rules: Rules, now: float, window_h: float, fresh_h: float):
    """측정 구간 안의 기사를 해시·단어 집합으로 바꾼다 (제목 원문은 돌려주지 않는다).

    돌려주는 값: (items, stats). stats 에는 구간 기사 수, 신선 구간 기사 수, 날짜 없는 기사 수, 미래 시각 기사 수.
    """
    items, in_fresh, undated, future = [], 0, 0, 0
    for it in res.items:
        if it.ts is None:
            undated += 1
        else:
            if now - it.ts > window_h * 3600:
                continue
            if it.ts > now + 1800:
                future += 1
            if now - it.ts <= fresh_h * 3600:
                in_fresh += 1
        e = extract(it.title, rules)
        toks = frozenset(k for k in e.keywords if " " not in k and len(k) >= 2)
        items.append(AItem(feed.id, feed.pub, e.hash, toks, e.political))
    return items, {"window": len(items), "fresh": in_fresh, "undated": undated, "future": future}


class StoryIndex:
    """같은 사건의 기사를 찾는 색인. 제목 해시 일치(같은 기사) 또는 단어 집합 겹침(같은 사건)."""

    def __init__(self, items: list[AItem], nd: dict):
        self.items = [i for i in items if not i.political]
        self.nd = nd
        self.by_hash: dict[str, list[int]] = defaultdict(list)
        df: Counter = Counter()
        for n, it in enumerate(self.items):
            self.by_hash[it.hash].append(n)
            df.update(it.tokens)
        cut = max(20, nd["common_token_share"] * len(self.items))
        self.inv: dict[str, list[int]] = defaultdict(list)
        for n, it in enumerate(self.items):
            for t in it.tokens:
                if df[t] <= cut:
                    self.inv[t].append(n)

    def match(self, item: AItem, skip=None) -> str | None:
        """'exact' | 'near' | None. skip(other)가 True 인 기사는 비교에서 뺀다 (자기 피드 등)."""
        for n in self.by_hash.get(item.hash, ()):
            if not skip or not skip(self.items[n]):
                return "exact"
        shared: Counter = Counter()
        for t in item.tokens:
            for n in self.inv.get(t, ()):
                shared[n] += 1
        for n, c in shared.items():
            if c < self.nd["min_shared_tokens"]:
                continue
            other = self.items[n]
            if skip and skip(other):
                continue
            inter = len(item.tokens & other.tokens)
            union = len(item.tokens) + len(other.tokens) - inter
            if union and inter / union >= self.nd["min_jaccard"]:
                return "near"
        return None


@dataclasses.dataclass
class Measure:
    kind: str                 # current | candidate
    feed: Feed
    category: str
    status: str = ""
    http: int | None = None
    ms: int | None = None     # 응답 시간(밀리초, 마지막 시도의 요청 시간)
    robots: str = ""
    error: str = ""
    items_window: int = 0
    items_fresh: int = 0
    undated: int = 0
    future: int = 0
    newest_age_h: float | None = None
    hangul: float = 0.0
    political_share: float = 0.0
    n_eval: int = 0           # 정치 제외 후 비교 대상 기사 수
    dup_exact: float = 0.0    # 현재 피드(후보) 또는 다른 현재 피드(현재)와 같은 기사 비율
    dup_near: float = 0.0     # 같은 기사 + 같은 사건 비율
    new_rate: float = 0.0     # 1 - dup_near
    marginal_gain: int | None = None
    rank: int | None = None
    verdict: str = ""
    reason: str = ""


def measure_feed(kind: str, feed: Feed, res: FetchResult, ms: int | None, rules: Rules, now: float,
                 cfg: dict, window_h: float, category: str):
    th = cfg["thresholds"]
    m = Measure(kind, feed, category, status=res.status, http=res.http, ms=ms, robots=res.robots, error=res.error)
    items: list[AItem] = []
    if res.ok and res.items:
        items, st = build_items(feed, res, rules, now, window_h, th["fresh_hours"])
        m.items_window, m.items_fresh, m.undated, m.future = st["window"], st["fresh"], st["undated"], st["future"]
        m.hangul = hangul_ratio([i.title for i in res.items])
        m.political_share = round(sum(i.political for i in items) / len(items), 3) if items else 0.0
        m.n_eval = sum(not i.political for i in items)
    if res.newest_ts:
        m.newest_age_h = round((now - res.newest_ts) / 3600, 1)
    return m, items


def classify(m: Measure, cfg: dict) -> tuple[str, str]:
    """후보 한 개의 1차 판정(순위 반영 전). 정상이면 ('통과','')."""
    th = cfg["thresholds"]
    if m.status in ("robots_blocked", "robots_unavailable"):
        return "제외", "robots.txt 불허/확인 불가"
    if m.status not in ("ok", "not_modified"):
        return "제외", f"접근 실패({m.status}{' ' + str(m.http) if m.http else ''})"
    if m.items_window == 0 and m.newest_age_h is not None and m.newest_age_h > th["stale_exclude_hours"]:
        return "제외", f"정지(최신 기사 {m.newest_age_h}시간 전)"
    if m.hangul < th["min_hangul_ratio"]:
        return "제외", "비한국어 (추출기는 한국어용)"
    if m.items_window and m.political_share >= th["max_political_share"]:
        return "제외", f"정치 위주 (정치 {m.political_share:.0%})"
    if m.newest_age_h is not None and m.newest_age_h > th["stale_exclude_hours"]:
        return "제외", f"정지(최신 기사 {m.newest_age_h}시간 전)"
    if m.newest_age_h is None:
        return "보류", "기사 날짜 없음(신선도 판단 불가)"
    if m.newest_age_h > th["fresh_hours"]:
        return "보류", f"발행 드묾(최신 기사 {m.newest_age_h}시간 전)"
    if m.items_window < th["min_items_window"]:
        return "보류", f"기사 드묾(구간 {m.items_window}건)"
    if m.dup_near >= th["max_dup_near"]:
        return "제외", f"기존 피드와 중복 {m.dup_near:.0%}"
    return "통과", ""


def greedy_rank(base: list[AItem], cands: dict[str, list[AItem]], nd: dict) -> list[tuple[str, int]]:
    """이미 있는 피드(base)와, 앞서 고른 후보에 없는 기사를 가장 많이 가져오는 후보부터 순서대로 고른다.

    후보끼리 겹치는 기사는 한 번만 센다. 반환: [(후보 id, 순수 신규 기사 수)] 순위순.
    """
    base_idx = StoryIndex(base, nd)
    unc = {fid: [it for it in items if not it.political and not base_idx.match(it)] for fid, items in cands.items()}
    remaining, out = set(cands), []
    while remaining:
        best = max(sorted(remaining), key=lambda f: len(unc[f]))
        out.append((best, len(unc[best])))
        remaining.discard(best)
        idx = StoryIndex(cands[best], nd)
        for f in remaining:
            unc[f] = [it for it in unc[f] if not idx.match(it)]
    return out


def hhi(counts: dict) -> float:
    total = sum(counts.values())
    return round(sum((c / total) ** 2 for c in counts.values()), 3) if total else 0.0


def distribution(rows: list[Measure], extra: dict[str, int] | None = None, key: str = "pub") -> dict[str, int]:
    """매체(pub) 또는 분야(category)별 기사 수. extra 는 (추가 검토 피드의 순수 신규 기사처럼) 더할 몫."""
    c: Counter = Counter()
    for m in rows:
        k = m.feed.pub if key == "pub" else m.category
        c[k] += m.items_window
    for k, v in (extra or {}).items():
        c[k] += v
    return dict(c)


# ---------------------------------------------------------------- 실행 본체

def run_audit(settings: dict, acfg: dict, current: list[Feed], cands: list[Feed], fetcher, rules: Rules,
              now: float, timing: dict | None = None, workers: int | None = None) -> dict:
    """현재 피드와 후보를 모두 측정하고 중복률·순위·판정·분포를 계산한다. 파일은 쓰지 않는다."""
    from concurrent.futures import ThreadPoolExecutor

    window_h = settings["max_item_age_hours"]
    nd, th = acfg["near_dup"], acfg["thresholds"]
    feeds = [("current", f) for f in current] + [("candidate", f) for f in cands]
    with ThreadPoolExecutor(max_workers=workers or settings["max_workers"]) as ex:
        results = list(ex.map(lambda kf: fetcher.fetch(kf[1], now=now, use_conditional=False), feeds))

    measures: list[Measure] = []
    items_by: dict[str, list[AItem]] = {}
    for (kind, f), res in zip(feeds, results):
        m, items = measure_feed(kind, f, res, (timing or {}).get(f.url), rules, now, acfg, window_h,
                                category_of(f, kind, acfg))
        measures.append(m)
        items_by[f.id] = items

    cur = [m for m in measures if m.kind == "current"]
    can = [m for m in measures if m.kind == "candidate"]
    base_items = [it for m in cur for it in items_by[m.feed.id]]
    base_idx = StoryIndex(base_items, nd)

    def rates(items, skip_for):
        ev = [i for i in items if not i.political]
        if not ev:
            return 0.0, 0.0
        ex_n = ne_n = 0
        for it in ev:
            hit = base_idx.match(it, skip_for(it))
            ex_n += hit == "exact"
            ne_n += hit is not None
        return round(ex_n / len(ev), 3), round(ne_n / len(ev), 3)

    for m in cur:   # 현재 피드: 다른 매체 피드와의 중복률 (같은 매체의 섹션 피드끼리는 제외)
        m.dup_exact, m.dup_near = rates(items_by[m.feed.id], lambda it: (lambda o: o.pub == it.pub))
        m.new_rate = round(1 - m.dup_near, 3)
        m.verdict = "정상" if m.status in ("ok", "not_modified") and (m.newest_age_h is None or m.newest_age_h <= th["fresh_hours"]) \
            else ("정지 의심" if m.status in ("ok", "not_modified") else "실패")
    for m in can:   # 후보: 현재 피드 전체와의 중복률
        m.dup_exact, m.dup_near = rates(items_by[m.feed.id], lambda it: None)
        m.new_rate = round(1 - m.dup_near, 3) if m.n_eval else 0.0
        m.verdict, m.reason = classify(m, acfg)

    elig = {m.feed.id: items_by[m.feed.id] for m in can if m.verdict == "통과"}
    ranking = greedy_rank(base_items, elig, nd) if elig else []
    by_id = {m.feed.id: m for m in can}
    gains: dict[str, int] = {}
    for n, (fid, gain) in enumerate(ranking, 1):
        m = by_id[fid]
        m.rank, m.marginal_gain = n, gain
        gains[fid] = gain
        if gain >= th["min_marginal_gain"]:
            m.verdict, m.reason = "추가 검토", f"순수 신규 기사 {gain}건"
        else:
            m.verdict, m.reason = "보류", f"신규 기여 낮음(순수 신규 {gain}건)"

    added = [by_id[f] for f, g in ranking if g >= th["min_marginal_gain"]]
    extra_pub, extra_cat = Counter(), Counter()
    for m in added:
        extra_pub[m.feed.pub] += gains[m.feed.id]
        extra_cat[m.category] += gains[m.feed.id]
    out = {
        "now": now, "window_h": window_h, "measures": measures, "ranking": ranking, "added": added,
        "dist": {
            "pub_before": distribution(cur), "pub_after": distribution(cur, extra_pub),
            "cat_before": distribution(cur, key="category"), "cat_after": distribution(cur, extra_cat, key="category"),
        },
    }
    for k in ("pub", "cat"):
        out["dist"][f"{k}_hhi_before"] = hhi(out["dist"][f"{k}_before"])
        out["dist"][f"{k}_hhi_after"] = hhi(out["dist"][f"{k}_after"])
    return out


# ---------------------------------------------------------------- 출력 (제목 원문은 들어 있지 않다)

FEED_COLS = ["kind", "id", "publisher", "section_or_group", "category", "url", "status", "http", "response_ms",
             "robots", "items_window", "items_fresh", "undated", "future_items", "newest_age_h", "hangul_ratio",
             "political_share", "dup_exact", "dup_near", "new_rate", "marginal_gain", "rank", "verdict", "reason", "error"]


def feed_rows(result: dict) -> list[dict]:
    rows = []
    for m in result["measures"]:
        rows.append({"kind": m.kind, "id": m.feed.id, "publisher": m.feed.pub, "section_or_group": m.feed.section,
                     "category": m.category, "url": m.feed.url, "status": m.status, "http": m.http or "",
                     "response_ms": m.ms if m.ms is not None else "", "robots": m.robots,
                     "items_window": m.items_window, "items_fresh": m.items_fresh, "undated": m.undated,
                     "future_items": m.future,
                     "newest_age_h": m.newest_age_h if m.newest_age_h is not None else "",
                     "hangul_ratio": m.hangul, "political_share": m.political_share, "dup_exact": m.dup_exact,
                     "dup_near": m.dup_near, "new_rate": m.new_rate,
                     "marginal_gain": m.marginal_gain if m.marginal_gain is not None else "",
                     "rank": m.rank or "", "verdict": m.verdict, "reason": m.reason, "error": m.error[:120]})
    return rows


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _share_table(before: dict, after: dict, title: str, top: int = 12) -> list[str]:
    tb, ta = sum(before.values()) or 1, sum(after.values()) or 1
    keys = sorted(set(before) | set(after), key=lambda k: -(after.get(k, 0)))
    lines = [f"| {title} | 현재 기사 수 | 현재 비중 | 추가 후 기사 수 | 추가 후 비중 |", "|---|---:|---:|---:|---:|"]
    for k in keys[:top]:
        lines.append(f"| {k} | {before.get(k, 0)} | {_pct(before.get(k, 0) / tb)} | {after.get(k, 0)} | {_pct(after.get(k, 0) / ta)} |")
    if len(keys) > top:
        rest_b = sum(before.get(k, 0) for k in keys[top:])
        rest_a = sum(after.get(k, 0) for k in keys[top:])
        lines.append(f"| (그 외 {len(keys) - top}개) | {rest_b} | {_pct(rest_b / tb)} | {rest_a} | {_pct(rest_a / ta)} |")
    return lines


def render_report(result: dict, acfg: dict, stamp: str) -> str:
    ms = result["measures"]
    cur = [m for m in ms if m.kind == "current"]
    can = [m for m in ms if m.kind == "candidate"]
    th = acfg["thresholds"]
    L = [f"# RSS 출처 감사 보고서 ({stamp})", "",
         "읽기 전용 감사다. `feeds.yaml`·수집 설정·`data/` 는 바뀌지 않았고, 신규 후보는 수집 대상이 아니다. "
         "헤드라인 원문은 이 보고서와 산출 파일 어디에도 없다 (메모리에서 키워드·해시만 계산).",
         f"측정 구간: 최근 {result['window_h']}시간 기사(날짜 없는 기사 포함). **한 시점의 스냅샷 1회**이므로 "
         "시간대를 달리해 한 번 더 실행해 비교하는 것을 권한다.", ""]

    ok_c = [m for m in cur if m.verdict == "정상"]
    L += ["## 1. 요약", "",
          f"- 현재 활성 피드 {len(cur)}개: 정상 {len(ok_c)}, 정지 의심 {sum(m.verdict == '정지 의심' for m in cur)}, "
          f"실패 {sum(m.verdict == '실패' for m in cur)}",
          f"- 신규 후보 {len(can)}개: **추가 검토 {sum(m.verdict == '추가 검토' for m in can)}**, "
          f"보류 {sum(m.verdict == '보류' for m in can)}, 제외 {sum(m.verdict == '제외' for m in can)}", ""]

    bad = [m for m in cur if m.verdict != "정상"]
    L += ["## 2. 현재 피드 상태", ""]
    if bad:
        L += ["| 판정 | 피드 | 상태 | HTTP | 최신 기사(시간 전) | 비고 |", "|---|---|---|---|---|---|"]
        for m in bad:
            L.append(f"| {m.verdict} | {m.feed.id} | {m.status} | {m.http or ''} | "
                     f"{m.newest_age_h if m.newest_age_h is not None else ''} | {m.error[:60]} |")
    else:
        L.append("모든 현재 피드가 정상이다.")
    resp = sorted(m.ms for m in cur if m.ms is not None)
    if resp:
        L += ["", f"응답 시간(요청 1회): 중앙값 {resp[len(resp) // 2]}ms, 최대 {resp[-1]}ms."]
    odd_tz = [m.feed.id for m in cur if m.future]
    if odd_tz:
        L += ["", f"미래 시각 기사가 있는 현재 피드(시간대 어긋남 의심): {', '.join(odd_tz)}"]
    dup_hi = sorted((m for m in cur if m.n_eval >= 5), key=lambda m: -m.dup_near)[:8]
    L += ["", "다른 매체 피드와의 중복이 많은 현재 피드 (상위 8, 같은 매체끼리는 제외):", "",
          "| 피드 | 비교 기사 | 같은 기사 | 같은 사건 포함 |", "|---|---:|---:|---:|"]
    for m in dup_hi:
        L.append(f"| {m.feed.id} | {m.n_eval} | {_pct(m.dup_exact)} | {_pct(m.dup_near)} |")

    L += ["", "## 3. 후보 접근 결과", "", "| 후보 | 분류 | 상태 | HTTP | 응답(ms) | 기사(구간) | 최신(시간 전) | 한글 | 정치 |",
          "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for m in sorted(can, key=lambda m: (m.status not in ("ok", "not_modified"), m.feed.pub)):
        L.append(f"| {m.feed.pub} | {m.category} | {m.status} | {m.http or ''} | {m.ms if m.ms is not None else ''} | "
                 f"{m.items_window} | {m.newest_age_h if m.newest_age_h is not None else ''} | {m.hangul:.2f} | {_pct(m.political_share)} |")

    L += ["", "## 4. 매체별·분야별 분포", "",
          f"매체 쏠림 지수(HHI, 낮을수록 고름): 현재 {result['dist']['pub_hhi_before']} → 추가 검토 피드 반영 후 "
          f"{result['dist']['pub_hhi_after']}. 분야 HHI: {result['dist']['cat_hhi_before']} → {result['dist']['cat_hhi_after']}.",
          "추가 후 수치는 추가 검토 피드의 **순수 신규 기사**만 더한 값이다 (중복 기사는 다양성을 늘리지 않으므로 제외).",
          "분야는 피드가 선언한 분야 기준이라 종합 피드의 내부 주제 분포는 반영하지 못한다.", ""]
    L += _share_table(result["dist"]["pub_before"], result["dist"]["pub_after"], "매체")
    L += [""] + _share_table(result["dist"]["cat_before"], result["dist"]["cat_after"], "분야", top=20)

    L += ["", "## 5. 신규 기사 제공 비율과 기존 피드와의 중복률 (후보)", "",
          "`같은 기사` = 정규화 제목 해시 일치. `같은 사건 포함` = 해시 일치 또는 핵심 단어 집합이 충분히 겹침. "
          "`신규 비율` = 1 − 같은 사건 포함 (정치 기사는 제외하고 계산).", "",
          "| 후보 | 비교 기사 | 같은 기사 | 같은 사건 포함 | 신규 비율 |", "|---|---:|---:|---:|---:|"]
    for m in sorted((m for m in can if m.n_eval), key=lambda m: -m.new_rate):
        L.append(f"| {m.feed.pub} | {m.n_eval} | {_pct(m.dup_exact)} | {_pct(m.dup_near)} | {_pct(m.new_rate)} |")

    L += ["", "## 6. 추가 가치 순위", "",
          "기존 피드와, 앞서 고른 후보까지 반영하고도 남는 **순수 신규 기사 수**가 큰 순서로 고른다 (후보끼리 겹치는 기사는 한 번만 센다). "
          f"순수 신규 {th['min_marginal_gain']}건 미만이면 보류.", "",
          "| 순위 | 후보 | 분류 | 순수 신규 기사 | 누적 | 신규 비율 | 판정 |", "|---:|---|---|---:|---:|---:|---|"]
    cum = 0
    for n, (fid, gain) in enumerate(result["ranking"], 1):
        m = next(x for x in can if x.feed.id == fid)
        cum += gain
        L.append(f"| {n} | {m.feed.pub} | {m.category} | {gain} | {cum} | {_pct(m.new_rate)} | {m.verdict} |")

    L += ["", "## 7. 최종 분류안 (피드 목록 변경은 별도 승인 후)", ""]
    for verdict in ("추가 검토", "보류", "제외"):
        rows = [m for m in can if m.verdict == verdict]
        L += [f"### {verdict} ({len(rows)}개)", ""]
        if not rows:
            L += ["(없음)", ""]
            continue
        L += ["| 후보 | 분류 | URL | 사유 |", "|---|---|---|---|"]
        order = sorted(rows, key=lambda m: (m.rank or 9999, m.feed.pub))
        for m in order:
            L.append(f"| {m.feed.pub} | {m.category} | {m.feed.url} | {m.reason} |")
        L.append("")

    L += ["## 8. 해석 시 주의", "",
          "- 한 번의 스냅샷이라 발행이 드문 매체가 불리하게 보이고, 사건이 몰린 날에는 중복률이 높게 나온다.",
          "- 근사 중복은 단어 겹침 기준이라 제목이 짧으면 중복을 놓칠 수 있다(신규 비율이 약간 높게 나옴).",
          "- 분야 분포는 피드 선언 기준이다. 실제 주제 다양성은 추가 후 키워드 수집 결과로 다시 확인해야 한다.",
          "- 이 보고서는 어떤 피드도 추가하지 않았다. 추가는 사용자 승인 후 `feeds.yaml` 변경으로만 이루어진다.", ""]
    return "\n".join(L)
