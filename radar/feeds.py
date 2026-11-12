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
    naive_kst: bool = False   # 기사 시각을 시간대 표시 없이 한국 시각으로 적는 피드. UTC 로 읽혀 9시간 앞서므로 항상 9시간을 되돌린다


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


def parse_feed_bytes(feed_id: str, content: bytes, now: float, stale_hours: float,
                     naive_kst: bool = False) -> FetchResult:
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
    # 시간대 표시 없이 한국 시각을 적는 피드는 UTC로 읽혀 9시간 미래로 보인다.
    #  - feeds.yaml 에 naive_kst: true 로 표시한 피드는 항상 9시간을 되돌린다 (기사가 드문 분야 피드도 정확히 보정).
    #  - 표시가 없는 피드는 최근 기사의 25% 이상이 미래일 때만 자동으로 되돌린다 (안전망).
    dated = [i for i in items if i.ts]
    future = [i for i in dated if i.ts > now + 1800]
    auto = (len(dated) >= 3 and len(future) >= 0.25 * len(dated)
            and max(i.ts for i in dated) - now <= 9.5 * 3600)
    if dated and (naive_kst or auto):
        for i in dated:
            i.ts -= 9 * 3600
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
    """서버별 robots.txt를 한 번만 읽어 허용 여부를 판단한다.

    잠금 설계:
      _cache_lock  — _cache 딕셔너리 읽기/쓰기 보호(매우 짧게 보유).
      _host_locks  — 호스트별 잠금. 같은 호스트의 중복 요청은 직렬화하되,
                     다른 호스트 요청을 블로킹하지 않는다.
    일시적인 5xx/timeout/네트워크 오류는 'unavailable'로 돌려주고 캐시하지 않는다.
    명시적 Disallow는 'blocked', robots.txt 없음(404 등)은 'allowed'로 처리한다.
    """

    def __init__(self, session: requests.Session, ua: str, timeout: float, unavailable_ttl: float = 60.0):
        self.session, self.ua, self.timeout = session, ua, timeout
        self.unavailable_ttl = unavailable_ttl
        self._cache: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None, float | None]] = {}
        self._cache_lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_locks_guard = threading.Lock()

    def _get_host_lock(self, host: str) -> threading.Lock:
        with self._host_locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    def _get_cached(self, host: str, now: float) -> tuple[str, urllib.robotparser.RobotFileParser | None] | None:
        with self._cache_lock:
            cached = self._cache.get(host)
            if cached is None:
                return None
            state, rp, expires_at = cached
            if expires_at is not None and now >= expires_at:
                self._cache.pop(host, None)
                return None
            return state, rp

    def check(self, url: str) -> str:
        """'allowed' | 'blocked' | 'unavailable'"""
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        now = time.time()

        # 캐시 히트 — 만료되지 않은 캐시가 있으면 즉시 반환
        cached = self._get_cached(host, now)
        if cached is not None:
            state, rp = cached
            if state != "ok":
                return state
            return "allowed" if rp.can_fetch(self.ua, url) else "blocked"

        # 캐시 미스 — 호스트별 잠금으로 중복 요청 방지
        host_lock = self._get_host_lock(host)
        with host_lock:
            # 잠금 대기 중 다른 스레드가 채웠을 수 있으므로 재확인
            now = time.time()
            cached = self._get_cached(host, now)
            if cached is not None:
                state, rp = cached
                if state != "ok":
                    return state
                return "allowed" if rp.can_fetch(self.ua, url) else "blocked"

            state, rp, expires_at = self._load(host)
            with self._cache_lock:
                self._cache[host] = (state, rp, expires_at)
            if state != "ok":
                return state
            return "allowed" if rp.can_fetch(self.ua, url) else "blocked"

    @staticmethod
    def _parse_retry_after(val: str | None, default: float = 60.0) -> float:
        if not val:
            return default
        val = val.strip()
        try:
            return max(0.0, float(val))
        except ValueError:
            pass
        try:
            import email.utils
            parsed = email.utils.parsedate_to_datetime(val)
            return max(0.0, parsed.timestamp() - time.time())
        except Exception:
            pass
        return default

    def _load(self, host: str) -> tuple[str, urllib.robotparser.RobotFileParser | None, float | None]:
        """robots.txt를 가져온다. 일시 장애는 재시도 및 Retry-After/TTL 동안 캐시."""
        for attempt in range(2):
            try:
                r = self.session.get(host + "/robots.txt", timeout=self.timeout)
            except requests.RequestException:
                if attempt == 0:
                    continue
                return ("unavailable", None, time.time() + self.unavailable_ttl)

            # 429 Too Many Requests: Retry-After 헤더 확인 후 재시도 또는 만료 캐시
            if r.status_code == 429:
                retry_header = getattr(r, "headers", {}).get("Retry-After") if hasattr(r, "headers") else None
                if retry_header is not None:
                    retry_after = self._parse_retry_after(retry_header, default=self.unavailable_ttl)
                    if attempt == 0 and retry_after <= 2.0:
                        time.sleep(retry_after)
                        continue
                    return ("unavailable", None, time.time() + retry_after)
                else:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    return ("unavailable", None, time.time() + self.unavailable_ttl)

            # 5xx 서버 오류: 1회 재시도 후 기본 TTL 동안 일시 장애 캐시
            if r.status_code >= 500:
                if attempt == 0:
                    continue
                return ("unavailable", None, time.time() + self.unavailable_ttl)

            rp = urllib.robotparser.RobotFileParser()
            # 4xx 클라이언트 오류 (403, 404, 410 등): RFC 9309 2.3.1.2에 따라 robots.txt 부재 및 제한 없음 간주
            if r.status_code >= 400:
                rp.parse([])
            else:
                rp.parse(r.text.splitlines())
            return ("ok", rp, None)

        return ("unavailable", None, time.time() + self.unavailable_ttl)


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
                res = parse_feed_bytes(feed.id, r.content, now, self.cfg["stale_feed_hours"], feed.naive_kst)
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
    <dir>/<feed_id>.not_modified 304 응답 (가장 먼저 검사)
    파일이 없으면 http_error(404)로 처리한다.
    """

    def __init__(self, directory: str | Path, cfg: dict):
        self.dir, self.cfg = Path(directory), cfg

    def fetch(self, feed: Feed, now: float | None = None, use_conditional: bool = True) -> FetchResult:
        now = now or time.time()
        if (self.dir / f"{feed.id}.not_modified").exists():
            return FetchResult(feed.id, "not_modified", http=304)
        for status in ("robots_blocked", "timeout", "network_error", "http_error"):
            if (self.dir / f"{feed.id}.{status}").exists():
                return FetchResult(feed.id, status, http=503 if status == "http_error" else None,
                                   error=f"fixture {status}")
        p = self.dir / f"{feed.id}.xml"
        if not p.exists():
            return FetchResult(feed.id, "http_error", http=404, error="HTTP 404 (fixture 없음)")
        res = parse_feed_bytes(feed.id, p.read_bytes(), now, self.cfg["stale_feed_hours"], feed.naive_kst)
        res.http = 200
        return res
