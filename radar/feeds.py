"""RSS 피드 설정 읽기, 접근(robots.txt 준수, 조건부 요청, 서버별 간격), 파싱.

상태(status) 값
  ok / not_modified      정상
  robots_blocked         robots.txt가 막고 있어 수집하지 않음
  robots_unavailable     robots.txt를 확인하지 못해 이번 회차는 건너뜀
  http_error / timeout / network_error  접근 실패
  parse_error            응답이 RSS로 해석되지 않음 (차단 페이지 등)
  empty                  RSS는 열리지만 항목이 없음
"""
from __future__ import annotations

import calendar
import dataclasses
import threading
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path

import feedparser
import requests
import yaml

OK_STATUSES = ("ok", "not_modified")


@dataclasses.dataclass
class Feed:
    id: str
    pub: str
    name: str
    section: str
    title: str
    url: str
    enabled: bool = True
    note: str = ""


@dataclasses.dataclass
class Item:
    title: str
    link: str
    ts: float | None  # epoch(UTC) 초, 없으면 None


@dataclasses.dataclass
class FetchResult:
    feed_id: str
    status: str
    http: int | None = None
    items: list = dataclasses.field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    error: str = ""
    newest_ts: float | None = None
    bozo: bool = False
    elapsed: float = 0.0
    stale: bool = False
    robots: str = ""

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES


def load_feeds(path: str | Path) -> list[Feed]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)["feeds"]
    names = {f.name for f in dataclasses.fields(Feed)}
    return [Feed(**{k: v for k, v in r.items() if k in names}) for r in raw]


def parse_feed_bytes(feed_id: str, content: bytes, now: float, stale_hours: float) -> FetchResult:
    """바이트로 받은 RSS/Atom을 해석한다. 중간에 잘린 XML도 가능한 만큼 읽는다."""
    parsed = feedparser.parse(content)
    items = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        ts = float(calendar.timegm(t)) if t else None
        items.append(Item(title=title, link=e.get("link", ""), ts=ts))
    res = FetchResult(feed_id=feed_id, status="ok", items=items, bozo=bool(parsed.get("bozo")))
    stamps = [i.ts for i in items if i.ts]
    res.newest_ts = max(stamps) if stamps else None
    if not items:
        res.status = "parse_error" if res.bozo else "empty"
        exc = parsed.get("bozo_exception")
        res.error = f"{type(exc).__name__}: {exc}"[:200] if exc else "항목 없음"
    elif res.newest_ts is not None and (now - res.newest_ts) > stale_hours * 3600:
        res.stale = True
    return res


class RobotsCache:
    """서버별 robots.txt를 한 번만 읽어 허용 여부를 판단한다."""

    def __init__(self, session: requests.Session, ua: str, timeout: float):
        self.session, self.ua, self.timeout = session, ua, timeout
        self._cache: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None]] = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> str:
        """'allowed' | 'blocked' | 'unavailable'"""
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        with self._lock:
            if host not in self._cache:
                self._cache[host] = self._load(host)
            state, rp = self._cache[host]
        if state != "ok":
            return state
        return "allowed" if rp.can_fetch(self.ua, url) else "blocked"

    def _load(self, host: str):
        try:
            r = self.session.get(host + "/robots.txt", timeout=self.timeout)
        except requests.RequestException:
            return ("unavailable", None)
        if r.status_code >= 500:
            return ("unavailable", None)
        rp = urllib.robotparser.RobotFileParser()
        if r.status_code >= 400:        # robots.txt 없음 = 제한 없음
            rp.parse([])
        else:
            rp.parse(r.text.splitlines())
        return ("ok", rp)


class HttpFetcher:
    def __init__(self, cfg: dict, http_state: dict | None = None):
        self.cfg = cfg
        self.http_state = http_state if http_state is not None else {}
        self.session = requests.Session()
        self.ua = cfg["user_agent"]
        self.session.headers.update(
            {"User-Agent": self.ua, "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.5"}
        )
        self.robots = RobotsCache(self.session, self.ua, cfg["timeout_sec"])
        self._host_lock: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _throttle(self, host: str):
        with self._guard:
            lock = self._host_lock.setdefault(host, threading.Lock())
        with lock:
            wait = self._host_last.get(host, 0) + self.cfg["host_min_interval_sec"] - time.time()
            if wait > 0:
                time.sleep(wait)
            self._host_last[host] = time.time()

    def fetch(self, feed: Feed, now: float | None = None, use_conditional: bool = True) -> FetchResult:
        now = now or time.time()
        t0 = time.time()
        verdict = self.robots.check(feed.url)
        if verdict == "blocked":
            return FetchResult(feed.id, "robots_blocked", error="robots.txt가 이 주소를 막음", robots=verdict)
        if verdict == "unavailable":
            return FetchResult(feed.id, "robots_unavailable", error="robots.txt를 확인하지 못함", robots=verdict)

        headers = {}
        cond = self.http_state.get(feed.id, {}) if use_conditional else {}
        if cond.get("etag"):
            headers["If-None-Match"] = cond["etag"]
        if cond.get("last_modified"):
            headers["If-Modified-Since"] = cond["last_modified"]

        host = urllib.parse.urlsplit(feed.url).netloc
        last_err = ""
        for attempt in range(self.cfg["retries"] + 1):
            self._throttle(host)
            try:
                r = self.session.get(feed.url, headers=headers, timeout=self.cfg["timeout_sec"])
            except requests.Timeout:
                last_err, status = "timeout", "timeout"
            except requests.RequestException as e:
                last_err, status = f"{type(e).__name__}: {e}"[:200], "network_error"
            else:
                if r.status_code == 304:
                    return FetchResult(feed.id, "not_modified", http=304, robots=verdict,
                                       elapsed=time.time() - t0)
                if r.status_code >= 500 and attempt < self.cfg["retries"]:
                    last_err, status = f"HTTP {r.status_code}", "http_error"
                    time.sleep(2)
                    continue
                if r.status_code >= 400:
                    return FetchResult(feed.id, "http_error", http=r.status_code, robots=verdict,
                                       error=f"HTTP {r.status_code}", elapsed=time.time() - t0)
                res = parse_feed_bytes(feed.id, r.content, now, self.cfg["stale_feed_hours"])
                res.http, res.robots, res.elapsed = r.status_code, verdict, time.time() - t0
                res.etag, res.last_modified = r.headers.get("ETag"), r.headers.get("Last-Modified")
                if res.status == "parse_error":
                    res.error = f"{res.error} (content-type: {r.headers.get('Content-Type', '?')})"[:200]
                return res
            if attempt < self.cfg["retries"]:
                time.sleep(2)
        return FetchResult(feed.id, status, error=last_err, robots=verdict, elapsed=time.time() - t0)


class FixtureFetcher:
    """네트워크 없이 로컬 파일로 피드를 흉내 낸다 (테스트·시뮬레이션용).

    <dir>/<feed_id>.xml          정상 응답
    <dir>/<feed_id>.<status>     http_error / timeout / robots_blocked 같은 실패 상황
    파일이 없으면 http_error(404)로 처리한다.
    """

    def __init__(self, directory: str | Path, cfg: dict):
        self.dir, self.cfg = Path(directory), cfg

    def fetch(self, feed: Feed, now: float | None = None, use_conditional: bool = True) -> FetchResult:
        now = now or time.time()
        for status in ("robots_blocked", "timeout", "network_error", "http_error"):
            if (self.dir / f"{feed.id}.{status}").exists():
                return FetchResult(feed.id, status, http=503 if status == "http_error" else None,
                                   error=f"fixture {status}")
        p = self.dir / f"{feed.id}.xml"
        if not p.exists():
            return FetchResult(feed.id, "http_error", http=404, error="HTTP 404 (fixture 없음)")
        res = parse_feed_bytes(feed.id, p.read_bytes(), now, self.cfg["stale_feed_hours"])
        res.http = 200
        return res
