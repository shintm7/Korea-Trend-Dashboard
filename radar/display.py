"""화면 표시 계층. 저장된 일일 집계를 읽어 사람이 보기 좋게 정리한다.

원칙: **원본은 그대로 두고, 화면 표시와 분석을 분리한다.**
- 이 모듈은 파일을 쓰지 않고 입력 행을 바꾸지 않는다 (복사본만 다룬다).
- 수집(pipeline)·저장(store)·품질 판정(quality) 은 이 모듈을 가져오지 않는다 (테스트로 확인).
- 포함 관계 그룹은 화면에서만 묶는다. 원본 키워드의 시계열을 합치거나 짧은 키워드로 대체하지 않으며,
  그룹에 속한 키워드를 모두 `members` 로 돌려주고 키워드별 시계열은 `member_series()` 로 따로 읽는다.
- 그룹의 기사 수는 구성 키워드 기사 수의 최댓값이다 (합이 아님). 구성 키워드가 같은 기사를 공유하므로 합하면 중복 집계된다.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from . import store
from .util import ROOT, read_wordlist

DEFAULT_PATH = ROOT / "config" / "display.yaml"


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path else DEFAULT_PATH
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ps = cfg.get("place_suffix", {})
    places = ps.get("places_file")
    cfg["_places"] = set(read_wordlist(path.parent / places)) if ps.get("enabled") and places else set()
    return cfg


def normalize_place(keyword: str, places: set) -> str:
    """표시 라벨용: 어절이 `장소 + 서` 꼴이고 장소가 허용 목록에 있을 때만 `서`를 뗀다."""
    out = []
    for tok in keyword.split(" "):
        if len(tok) >= 2 and tok.endswith("서") and tok[:-1] in places:
            tok = tok[:-1]
        out.append(tok)
    return " ".join(out)


def _group_nested(rows: list[dict]) -> list[list[dict]]:
    """구절 키워드가 구성 단어와 기사 수가 같으면 그 단어의 그룹에 넣는다. 한 구절은 한 그룹에만 속한다."""
    by_kw = {r["keyword"]: r for r in rows}
    groups: dict[str, list[dict]] = {}
    in_group: set[str] = set()
    for r in rows:
        words = r["keyword"].split(" ")
        if len(words) < 2:
            continue
        comps = [by_kw[w] for w in words if w in by_kw and by_kw[w]["articles"] == r["articles"]]
        if not comps:
            continue
        # 구성 단어가 둘 이상이면 결정적으로 하나를 고른다: 매체 수 큰 쪽, 같으면 사전순
        head = sorted(comps, key=lambda c: (-c["outlets"], c["keyword"]))[0]
        groups.setdefault(head["keyword"], [head]).append(r)
        in_group.add(head["keyword"])
        in_group.add(r["keyword"])
    out = list(groups.values())
    out += [[r] for r in rows if r["keyword"] not in in_group]
    return out


def _represent(members: list[dict]) -> dict:
    """대표어: 가장 긴 구절(같으면 기사 수 큰 쪽, 사전순)."""
    return sorted(members, key=lambda m: (-len(m["keyword"]), -m["articles"], m["keyword"]))[0]


def build_view(rows: list[dict], cfg: dict | None = None, min_articles: int = 1) -> list[dict]:
    """일일 집계 행 -> 표시용 항목 목록(순위순). 입력은 바꾸지 않는다.

    항목: label(대표어 표시 라벨), keyword(대표 원본 키워드), articles/outlets(구성 키워드 최댓값),
    members(원본 키워드와 각자의 집계), score, flags.
    """
    cfg = cfg or load_config()
    rows = [dict(r) for r in copy.deepcopy(rows) if r["articles"] >= min_articles]
    nest = cfg.get("nesting", {}).get("enabled", True)
    groups = _group_nested(rows) if nest else [[r] for r in rows]
    so = cfg.get("single_outlet", {})
    places = cfg.get("_places", set())
    items = []
    for members in groups:
        rep = _represent(members)
        articles = max(m["articles"] for m in members)
        outlets = max(m["outlets"] for m in members)
        flags = []
        score = float(articles)
        if so.get("enabled") and outlets <= so.get("max_outlets", 1) and articles >= so.get("min_articles", 8):
            flags.append("single_outlet")
            score *= so.get("score_factor", 0.25)
        label = normalize_place(rep["keyword"], places)
        if label != rep["keyword"]:
            flags.append("place_normalized")
        items.append({"label": label, "keyword": rep["keyword"], "articles": articles, "outlets": outlets,
                      "members": sorted(members, key=lambda m: (-m["articles"], m["keyword"])),
                      "score": score, "flags": flags})
    # 정규화로 라벨이 같아진 항목은 한 줄로 합쳐 보이되 구성 키워드는 모두 보존한다 (기사 수는 최댓값)
    merged: dict[str, dict] = {}
    for it in items:
        cur = merged.get(it["label"])
        if cur is None:
            merged[it["label"]] = it
            continue
        cur["members"] = sorted(cur["members"] + it["members"], key=lambda m: (-m["articles"], m["keyword"]))
        cur["articles"] = max(cur["articles"], it["articles"])
        cur["outlets"] = max(cur["outlets"], it["outlets"])
        cur["score"] = max(cur["score"], it["score"])
        cur["flags"] = sorted(set(cur["flags"]) | set(it["flags"]))
    return sorted(merged.values(), key=lambda i: (-i["score"], -i["articles"], i["label"]))


def day_view(data_dir: str | Path, day: str, cfg: dict | None = None, min_articles: int = 2) -> list[dict]:
    return build_view(store.read_daily(data_dir, day), cfg, min_articles)


def member_series(data_dir: str | Path, item: dict, days: list[str]) -> dict[str, dict[str, int]]:
    """그룹 구성 키워드마다 날짜별 기사 수. 키워드 시계열을 합치지 않고 각각 그대로 돌려준다.

    없는 날은 0. 읽기만 한다.
    """
    want = {m["keyword"] for m in item["members"]}
    out = {k: {} for k in want}
    for day in days:
        counts = {r["keyword"]: r["articles"] for r in store.read_daily(data_dir, day) if r["keyword"] in want}
        for k in want:
            out[k][day] = counts.get(k, 0)
    return out
