"""헤드라인 정규화와 키워드 추출.

키워드 = (1) 명사 단위(어절 안에서 이어진 명사 묶음과 그 구성 명사)
        (2) 이어진 2~3어절 명사 묶음 조합
헤드라인은 이 모듈을 거친 뒤 키워드와 해시만 남기고 원문은 버린다.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata

_BRACKET = re.compile(r"^\s*(?:[\[\(［（【<＜][^\]\)］）】>＞]{1,12}[\]\)］）】>＞]\s*)+")
_TRAIL = re.compile(r"\s*[\(（][^\)）]{1,8}[\)）]\s*$")
_NON_WORD = re.compile(r"[\W_]+")
_BREAK_END = tuple(",，…;:?!·ㆍ")
_CLOSERS = "'\"’”)]»》」』"

CONTENT = {"NNG", "NNP", "SL"}
NUMBER = {"SN"}

_kiwi = None


def get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
    return _kiwi


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = _BRACKET.sub("", t)
    t = _TRAIL.sub("", t)
    return t.strip()


def title_hash(norm: str) -> str:
    key = _NON_WORD.sub("", norm.lower())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclasses.dataclass
class Rules:
    stopwords: set
    political_tokens: set
    political_substrings: list
    ngram_max: int = 3
    max_keywords: int = 80

    @classmethod
    def from_lists(cls, stop: list[str], politics: list[str], ngram_max=3, max_keywords=80):
        tokens = {p for p in politics if not p.startswith("~")}
        subs = [p[1:] for p in politics if p.startswith("~")]
        return cls({s.casefold() for s in stop}, tokens, subs, ngram_max, max_keywords)


@dataclasses.dataclass
class Extraction:
    norm: str
    hash: str
    keywords: list
    political: bool


def _word_index(text: str) -> list[int]:
    idx, w, prev_space = [], -1, True
    for ch in text:
        if ch.isspace():
            idx.append(max(w, 0))
            prev_space = True
        else:
            if prev_space:
                w += 1
            idx.append(w)
            prev_space = False
    return idx


def _valid_unit(unit: str, rules: Rules) -> bool:
    if len(unit) < 2 or unit.isdigit():
        return False
    return unit.casefold() not in rules.stopwords


def extract(title: str, rules: Rules) -> Extraction:
    norm = normalize_title(title)
    h = title_hash(norm)
    if not norm:
        return Extraction(norm, h, [], False)

    # 정치 헤드라인(문자열 포함 방식)
    for s in rules.political_substrings:
        if s in norm:
            return Extraction(norm, h, [], True)

    toks = get_kiwi().tokenize(norm)
    char_word = _word_index(norm)
    words = norm.split()

    # 어절별로 이어진 명사 묶음(run)을 만든다
    runs_by_word: dict[int, list[list]] = {}
    cur: list = []
    cur_word = None

    def flush():
        nonlocal cur, cur_word
        if cur and any(t.tag in CONTENT for t in cur):
            runs_by_word.setdefault(cur_word, []).append(cur)
        cur, cur_word = [], None

    for tok in toks:
        wi = char_word[tok.start] if tok.start < len(char_word) else len(words) - 1
        if tok.tag in CONTENT or tok.tag in NUMBER:
            if cur and cur_word != wi:
                flush()
            cur.append(tok)
            cur_word = wi
        else:
            flush()
    flush()

    unigrams: list[str] = []
    political = False

    def add(u: str):
        if u not in unigrams:
            unigrams.append(u)

    word_unit: dict[int, str | None] = {}
    for wi, runs in runs_by_word.items():
        units = []
        for run in runs:
            # 숫자만 앞뒤에 붙은 경우는 숫자를 유지하되, 양끝의 숫자 단독 토큰은 버리지 않는다
            unit = "".join(t.form for t in run)
            if unit in rules.political_tokens:
                political = True
            comps = [t.form for t in run if t.tag in CONTENT]
            for c in comps:
                if c in rules.political_tokens:
                    political = True
            if _valid_unit(unit, rules):
                add(unit)
                units.append(unit)
            if len(comps) >= 2:
                for c in comps:
                    if _valid_unit(c, rules):
                        add(c)
        word_unit[wi] = units[0] if len(units) == 1 and len(runs) == 1 else None

    if political:
        return Extraction(norm, h, [], True)

    # 이어진 어절들의 명사 묶음으로 2~3어절 조합을 만든다
    ngrams: list[str] = []
    segment: list[str] = []

    def close_segment():
        nonlocal segment
        for n in range(2, rules.ngram_max + 1):
            for i in range(0, len(segment) - n + 1):
                g = " ".join(segment[i : i + n])
                if g not in ngrams:
                    ngrams.append(g)
        segment = []

    for wi in range(len(words)):
        u = word_unit.get(wi)
        if u is None:
            close_segment()
            continue
        segment.append(u)
        text = words[wi].rstrip(_CLOSERS)
        if text.endswith(_BREAK_END):
            close_segment()
    close_segment()

    kws = (unigrams + ngrams)[: rules.max_keywords]
    return Extraction(norm, h, kws, False)
