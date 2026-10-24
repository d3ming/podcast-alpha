"""Feed download + parse cache.

`load_feed(url)` returns {"link": show page, "entries": [plain dicts]} where each entry holds only the fields discovery
uses. Cost control, without changing what is found:

- Conditional GET: the ETag / Last-Modified of the last 200 is sent back; a 304 reuses the cached entries (the server just
  confirmed they are current). A 200 whose body hash matches the cache reuses the cached parse too.
- Parsing (feedparser, pure Python, the dominant cost) runs in a process pool, so feeds parse on separate cores.
- Any network error, HTTP error or unparseable body raises. A cache is never used to paper over a failed request, and one
  older than MAX_AGE is not even used for validators.

Cache files live in data/feed_cache/ (git-ignored with data/). PA_FEED_CACHE overrides the directory; set it to an empty
string to disable caching (always download and parse).
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

VERSION = 1  # bump when the reduced entry fields change
MAX_AGE = 30 * 86400  # a cache older than this is not trusted for conditional requests
ACCEPT = "application/atom+xml,application/rdf+xml,application/rss+xml,application/x-netcdf,application/xml;q=0.9,text/xml;q=0.2,*/*;q=0.1"
KEEP_LINK = ("href", "type", "rel")


def cache_dir() -> Path | None:
    env = os.environ.get("PA_FEED_CACHE")
    if env is not None:
        return Path(env) if env else None
    return Path(__file__).resolve().parent.parent / "data" / "feed_cache"


def _plain(v):
    return json.loads(json.dumps(v, default=str))


def reduce_entry(e) -> dict:
    """The fields ingest.from_rss, page_url, transcript_links and people.feed_transcripts read from a feedparser entry."""
    out = {k: str(e[k]) for k in ("id", "guid", "link", "title", "published", "updated", "itunes_duration") if e.get(k) is not None}
    summary = e.get("summary")
    if summary is not None:
        out["summary"] = str(summary)[:2000]  # from_rss keeps 2000 chars
    out["links"] = [{k: l[k] for k in KEEP_LINK if l.get(k) is not None} for l in e.get("links", []) or []]
    if e.get("podcast_transcript") is not None:
        out["podcast_transcript"] = _plain(e["podcast_transcript"])
    return out


def reduce_parsed(parsed) -> dict:
    """feedparser result -> plain picklable/JSON-able dict. `error` is set when the feed is empty and malformed or an HTTP error."""
    status, bozo = getattr(parsed, "status", None) or 0, getattr(parsed, "bozo", False)
    error = None
    if not parsed.entries and (bozo or status >= 400):
        error = f"HTTP {status}" if status >= 400 else str(getattr(parsed, "bozo_exception", "") or "malformed feed")[:160]
    return {"link": (parsed.get("feed") or {}).get("link"), "entries": [reduce_entry(e) for e in parsed.entries], "error": error}


def parse_bytes(body: bytes, headers: dict | None = None) -> dict:
    """Runs in a worker process. feedparser.parse on raw bytes needs the headers to pick the encoding and resolve relative URLs."""
    import feedparser

    out = reduce_parsed(feedparser.parse(body, response_headers=headers or {}))
    out["feedparser"] = feedparser.__version__
    return out


_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def _shutdown() -> None:
    with _pool_lock:
        pool = _pool
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown)


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(min(8, os.cpu_count() or 2))
        return _pool


def pool_parse(body: bytes, headers: dict | None = None) -> dict:
    """parse_bytes in the shared process pool; falls back to this process if the pool cannot start."""
    global _pool
    pool = _get_pool()
    try:
        return pool.submit(parse_bytes, body, headers).result()
    except BrokenProcessPool:
        with _pool_lock:
            if _pool is pool:
                _pool = None
        return parse_bytes(body, headers)


def _path(url: str, d: Path) -> Path:
    return d / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def _read(url: str, d: Path | None) -> dict | None:
    if d is None:
        return None
    try:
        c = json.loads(_path(url, d).read_text())
    except (OSError, ValueError):
        return None
    if c.get("v") != VERSION or c.get("url") != url or time.time() - c.get("saved", 0) > MAX_AGE:
        return None
    return c


def _write(url: str, d: Path | None, cache: dict) -> None:
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = _path(url, d)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, p)
    except OSError:
        pass  # a cache that cannot be written only costs speed


def load_feed(url: str, *, get=None, parse=None, directory: Path | None | str = "default", timeout: int = 30) -> dict:
    """Download (conditionally) and parse one feed. Raises ValueError/requests errors; never returns stale data on error."""
    if get is None:
        import requests

        get = requests.get
    parse = parse or pool_parse
    d = cache_dir() if directory == "default" else directory
    cached = _read(url, d)
    headers = {"User-Agent": "podcast-alpha (feedparser)", "Accept": ACCEPT, "Accept-Encoding": "gzip, deflate"}
    if cached and cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached and cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    r = get(url, headers=headers, timeout=timeout)
    if r.status_code == 304:
        if not cached:
            raise ValueError(f"no entries from {url}: HTTP 304 without a cached copy")
        return {"link": cached["link"], "entries": cached["entries"]}
    if r.status_code >= 400:
        raise ValueError(f"no entries from {url}: HTTP {r.status_code}")
    body = r.content
    sha = hashlib.sha256(body).hexdigest()
    if cached and cached.get("sha") == sha:
        parsed = {"link": cached["link"], "entries": cached["entries"]}
    else:
        parsed = parse(body, {"content-type": r.headers.get("content-type", ""), "content-location": getattr(r, "url", url) or url})
        if parsed.get("error"):
            raise ValueError(f"no entries from {url}: {parsed['error']}")
    _write(url, d, {"v": VERSION, "url": url, "saved": time.time(), "sha": sha, "etag": r.headers.get("etag"),
                    "last_modified": r.headers.get("last-modified"), "link": parsed["link"], "entries": parsed["entries"]})
    return {"link": parsed["link"], "entries": parsed["entries"]}
