import subprocess
from types import SimpleNamespace

from podcast_alpha import fetch, transcribe


def test_safe_transcript_swallows_lookup_errors(monkeypatch, capsys):
    def boom(ep, budget):
        raise subprocess.TimeoutExpired("yt-dlp", 120)

    monkeypatch.setattr(fetch, "get_transcript", boom)
    assert fetch.safe_transcript(SimpleNamespace(id="e1"), None) == (None, fetch.FAILED)
    assert "e1" in capsys.readouterr().out


def fake_search(lines):
    return lambda cmd, **kw: SimpleNamespace(stdout="\n".join(lines))


def test_youtube_mirror_requires_channel_to_look_like_the_show(monkeypatch):
    lines = ["aaaaaaaaaaa\tAI Trade Cracks and Anthropic IPO\tRandom Clips Channel",
             "bbbbbbbbbbb\tAI Trade Cracks and Anthropic IPO\tAll-In Podcast"]
    monkeypatch.setattr(subprocess, "run", fake_search(lines))
    assert transcribe.find_youtube_mirror("All-In Podcast", "AI Trade Cracks and Anthropic IPO") == "bbbbbbbbbbb"
    monkeypatch.setattr(subprocess, "run", fake_search(lines[:1]))
    assert transcribe.find_youtube_mirror("All-In Podcast", "AI Trade Cracks and Anthropic IPO") is None  # a clips channel


def test_youtube_mirror_rules(monkeypatch):
    t = "Why the AI Trade Cracked and What Anthropic Filed"
    run = lambda lines: monkeypatch.setattr(subprocess, "run", fake_search(lines))
    run([f"clip1111111\t{t}\tAll-In Clips"])
    assert transcribe.find_youtube_mirror("All-In Podcast", t) is None  # clips channel
    run([f"short111111\t{t} #shorts\tAll-In Podcast"])
    assert transcribe.find_youtube_mirror("All-In Podcast", t) is None
    run([f"loose111111\tAI trade cracked while Anthropic filed an S-1 and more\tSome Label"])
    assert transcribe.find_youtube_mirror("All-In Podcast", t) is None  # loose title on an unrelated channel
    run([f"real1111111\t{t}\tBloomberg Podcasts"])
    assert transcribe.find_youtube_mirror("Odd Lots", t) == "real1111111"  # network channel, exact title


def test_parallel_discover_keeps_source_order_caps_and_errors(monkeypatch):
    import time
    from datetime import datetime, timezone

    from podcast_alpha import ingest
    from podcast_alpha.config import Source
    from podcast_alpha.ingest import Episode

    now = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def ep(src, i):
        return Episode(id=f"{src.id}:{i}", source_id=src.id, source_name=src.name, tier=src.tier, title=f"{src.id} {i}", url="u",
                       published_at=now.replace(day=23 - i))

    def fake_find(src, now_, days):
        time.sleep({"a": 0.15, "b": 0.0, "c": 0.05}.get(src.id, 0))  # finish in the opposite order to the source list
        if src.id == "boom":
            raise RuntimeError("feed down")
        return [ep(src, i) for i in range(4)], "rss"

    monkeypatch.setattr(ingest, "find", fake_find)
    srcs = [Source("a", "A", 3), Source("boom", "Boom", 1), Source("b", "B", 3), Source("c", "C", 1)]
    health = {}
    eps = ingest.discover(srcs, now, 7, t3_cap=2, per_source=3, health=health)
    assert [e.id for e in eps] == ["a:0", "a:1", "c:0", "c:1", "c:2"]  # tier-3 cap of 2 is spent by the first tier-3 source
    assert health["boom"]["error"] == "feed down" and health["a"]["found"] == 4 and health["b"]["kept"] == 0


def test_youtube_block_stops_further_lookups(monkeypatch, capsys):
    import sys
    import types

    calls = []

    class IpBlocked(Exception):
        pass

    class Api:
        def fetch(self, vid, languages):
            calls.append(vid)
            raise IpBlocked()

    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=Api))
    monkeypatch.setattr(transcribe, "YT_BLOCKED", transcribe.threading.Event())
    assert transcribe.from_youtube("aaaaaaaaaaa") is None and transcribe.from_youtube("bbbbbbbbbbb") is None
    assert calls == ["aaaaaaaaaaa"]  # one request, no retries, nothing after the block
    assert capsys.readouterr().out.count("blocked by YouTube") == 1
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no search while blocked")))
    assert transcribe.find_youtube_mirror("Show", "Title") is None
