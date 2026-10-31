"""키워드 별칭 자동 병합과 이력 관리.

규칙 1 (space/case): 띄어쓰기·대소문자만 다른 표기는 하나로 합쳐 저장한다.
        "두바이쫀득쿠키" = "두바이 쫀득 쿠키", "ai" = "AI".
        합칠 때 대표 표기는 canon.json 에 기록해 날짜가 바뀌어도 같게 유지한다.
규칙 2 (initials): 3어절 키워드의 첫 글자를 이은 줄임말이 따로 자주 등장하면 별칭으로 본다.
        "두바이 쫀득 쿠키" -> "두쫀쿠". 원자료는 건드리지 않고 alias_map.json 에만 기록하며,
        분석 단계에서 apply_alias_map() 으로 합친다. 잘못 합쳐졌으면 alias_map.json 에서 줄을 지우면 된다.
모든 병합은 merge_log.csv 에 남는다.
"""
from __future__ import annotations

import collections
import csv
import json
import unicodedata
from pathlib import Path

LOG_FIELDS = ["date", "rule", "alias", "canonical", "alias_articles", "canonical_articles"]


def kw_key(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold().replace(" ", "")


def load_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path: str | Path, obj: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")


def canonicalize(kw_lists: list[list[str]], registry: dict) -> tuple[dict, list[dict]]:
    """표기만 다른 키워드를 대표 표기로 합치는 매핑을 만든다. registry 는 제자리에서 갱신된다."""
    forms: collections.Counter = collections.Counter()
    for kws in kw_lists:
        for k in set(kws):
            forms[k] += 1
    by_key: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for form, c in forms.items():
        by_key[kw_key(form)].append((form, c))

    mapping, logs = {}, []
    for key, lst in by_key.items():
        canon = registry.get(key)
        if canon is None and len(lst) > 1:
            canon = sorted(lst, key=lambda x: (-x[1], -x[0].count(" "), x[0]))[0][0]
            registry[key] = canon
        if canon is None:
            continue
        for form, c in lst:
            if form == canon:
                continue
            mapping[form] = canon
            rule = "space" if form.replace(" ", "") == canon.replace(" ", "") else "case"
            logs.append({"rule": rule, "alias": form, "canonical": canon,
                         "alias_articles": c, "canonical_articles": forms.get(canon, 0)})
    return mapping, logs


def detect_initials(rows: list[dict], alias_map: dict, cfg: dict) -> list[dict]:
    """줄임말(첫 글자 조합) 별칭 후보를 찾는다. 새로 찾은 것만 돌려준다."""
    uni = {r["keyword"]: r for r in rows if r["n"] == 1}
    best: dict[str, dict] = {}
    for r in rows:
        if r["n"] != 3 or r["articles"] < cfg["initials_min_articles"]:
            continue
        words = r["keyword"].split()
        ini = "".join(w[0] for w in words)
        if len(ini) < 3 or not all("가" <= c <= "힣" for c in ini) or ini in words or ini in alias_map:
            continue
        u = uni.get(ini)
        if not u or u["articles"] < cfg["initials_min_articles"] or u["outlets"] < cfg["initials_min_outlets"]:
            continue
        if ini not in best or r["articles"] > best[ini]["canonical_articles"]:
            best[ini] = {"rule": "initials", "alias": ini, "canonical": r["keyword"],
                         "alias_articles": u["articles"], "canonical_articles": r["articles"]}
    return list(best.values())


def apply_alias_map(rows: list[dict], alias_map: dict) -> list[dict]:
    """분석용: alias_map 으로 줄임말을 대표 키워드에 합친다. outlets 는 합집합을 알 수 없어 최댓값(하한)이다."""
    merged: dict[str, dict] = {}
    for r in rows:
        canon = alias_map.get(r["keyword"], r["keyword"])
        m = merged.setdefault(canon, {"keyword": canon, "n": canon.count(" ") + 1, "articles": 0, "outlets": 0})
        m["articles"] += r["articles"]
        m["outlets"] = max(m["outlets"], r["outlets"])
    return sorted(merged.values(), key=lambda r: (-r["articles"], -r["outlets"], r["keyword"]))


def append_log(path: str | Path, day: str, entries: list[dict]) -> None:
    if not entries:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        for e in entries:
            w.writerow({"date": day, **e})
