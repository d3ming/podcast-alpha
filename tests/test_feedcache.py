from datetime import datetime, timezone
from types import SimpleNamespace

import feedparser
import pytest
import requests

from podcast_alpha import feedcache, ingest
from podcast_alpha.config import Source

RSS = b"""<?xml version="1.0"?><rss xmlns:podcast="https://podcastindex.org/namespace/1.0"
xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel><link>https://show.example</link>
<item><guid>ep-new</guid><title> New one </title><link>/new</link><pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate>
<itunes:duration>01:02:03</itunes:duration><description>Hello</description>
<enclosure url="https://cdn.example/a.mp3" type="audio/mpeg" length="1"/>
<podcast:transcript url="https://x/t.vtt" type="text/vtt"/></item>
<item><guid>ep-old</guid><title>Old one</title><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
URL = "https://feed.example/rss"


def resp(status=200, body=RSS, **headers):
    return SimpleNamespace(status_code=status, content=body, headers=headers, url=URL)


class Server:
    """Records requests; replays the queued responses (an Exception instance is raised)."""
    def __init__(self, *queue):
        self.queue, self.sent = list(queue), []

    def __call__(self, url, headers=None, timeout=None):
        self.sent.append(headers)
        r = self.queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Parser:
    def __init__(self):
        self.calls = 0

    def __call__(self, body, headers=None):
        self.calls += 1
        return feedcache.parse_bytes(body, headers)


def test_304_reuses_cached_entries_without_parsing(tmp_path):
    srv, parse = Server(resp(etag='"v1"', **{"last-modified": "Mon, 19 Oct 2026 09:00:00 GMT"}), resp(304, b"")), Parser()
    first = feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    second = feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    assert second == first and parse.calls == 1
    assert srv.sent[1]["If-None-Match"] == '"v1"' and srv.sent[1]["If-Modified-Since"].startswith("Mon, 19 Oct")
    assert "If-None-Match" not in srv.sent[0]


def test_identical_body_without_validators_is_not_reparsed(tmp_path):
    srv, parse = Server(resp(), resp()), Parser()
    feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    assert parse.calls == 1
    srv.queue.append(resp(body=RSS.replace(b"Old one", b"Older one")))
    got = feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    assert parse.calls == 2 and got["entries"][1]["title"] == "Older one"


@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), requests.Timeout("slow"), resp(500, b""), resp(404, b"")])
def test_error_never_serves_the_cache(tmp_path, failure):
    srv, parse = Server(resp(etag='"v1"'), failure), Parser()
    feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    with pytest.raises((requests.RequestException, ValueError)):
        feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)


def test_empty_malformed_feed_raises(tmp_path):
    with pytest.raises(ValueError, match="no entries"):
        feedcache.load_feed(URL, get=Server(resp(body=b"<html>nope")), parse=Parser(), directory=tmp_path)


def test_304_without_a_cache_is_an_error_not_an_empty_feed(tmp_path):
    with pytest.raises(ValueError):
        feedcache.load_feed(URL, get=Server(resp(304, b"")), parse=Parser(), directory=tmp_path)


def test_old_cache_is_not_trusted_for_validators(tmp_path, monkeypatch):
    srv, parse = Server(resp(etag='"v1"'), resp(etag='"v2"')), Parser()
    feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    monkeypatch.setattr(feedcache, "MAX_AGE", -1)
    feedcache.load_feed(URL, get=srv, parse=parse, directory=tmp_path)
    assert "If-None-Match" not in srv.sent[1]


def test_cache_disabled_always_downloads_and_parses():
    srv, parse = Server(resp(), resp()), Parser()
    feedcache.load_feed(URL, get=srv, parse=parse, directory=None)
    feedcache.load_feed(URL, get=srv, parse=parse, directory=None)
    assert parse.calls == 2


def test_env_controls_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PA_FEED_CACHE", str(tmp_path))
    assert feedcache.cache_dir() == tmp_path
    monkeypatch.setenv("PA_FEED_CACHE", "")
    assert feedcache.cache_dir() is None


def test_cached_path_gives_the_same_episodes_as_a_direct_feedparser_parse(tmp_path):
    src = Source("s", "S", 1, rss=URL)
    direct = ingest.from_rss(src, NOW, 30, parsed=feedparser.parse(RSS, response_headers={"content-location": URL}))
    data = feedcache.load_feed(URL, get=Server(resp()), parse=feedcache.parse_bytes, directory=tmp_path)
    orig = feedcache.load_feed
    feedcache.load_feed = lambda url, **k: data
    try:
        via_cache = ingest.from_rss(src, NOW, 30)
    finally:
        feedcache.load_feed = orig
    assert via_cache == direct and len(direct) == 2
    new = next(e for e in direct if e.title == "New one")
    assert new.url == "https://feed.example/new" or new.url == "/new"  # whatever feedparser gives, both paths agree
    assert new.duration == 3723 and new.audio_url.endswith("a.mp3") and new.transcript_urls[0]["url"] == "https://x/t.vtt"


def test_process_pool_parse_matches_in_process():
    got = feedcache.pool_parse(RSS, {"content-location": URL})
    assert got == feedcache.parse_bytes(RSS, {"content-location": URL})
    assert [e["id"].rsplit("/", 1)[-1] for e in got["entries"]] == ["ep-new", "ep-old"]


def test_pool_created_once_across_threads(monkeypatch):
    import threading
    import time as _t

    made = []

    class FakePool:
        def __init__(self, n):
            _t.sleep(0.05)  # widen the race window
            made.append(self)

        def shutdown(self, **kw):
            pass

    monkeypatch.setattr(feedcache, "_pool", None)
    monkeypatch.setattr(feedcache, "ProcessPoolExecutor", FakePool)
    ts = [threading.Thread(target=feedcache._get_pool) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(made) == 1
