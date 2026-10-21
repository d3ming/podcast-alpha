from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from podcast_alpha import ingest, people, transcribe, whisper
from podcast_alpha.config import Source, load_sources
from podcast_alpha.ingest import Episode
from podcast_alpha.people import Person, feed_transcripts, load_people
from podcast_alpha.whisper import Budget, segments_until

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def ep(**kw):
    return Episode(**{"id": "rss:1", "source_id": "s", "source_name": "S", "tier": 1, "title": "T", "url": "u",
                      "published_at": NOW, **kw})


def test_sources_defaults_prefer_rss():
    sources, _ = load_sources("sources.yaml")
    assert all(s.preferred_source == "rss" and s.rss for s in sources)
    bg2 = next(s for s in sources if s.id == "bg2")
    assert bg2.order == ["rss"] and bg2.transcripts == ["podcast_transcript", "whisper"]
    assert Source("a", "A", 1, rss="x", fallback=["ytdlp"]).order == ["rss"]
    assert Source("a", "A", 1, rss="x", youtube="y", fallback=["ytdlp"]).order == ["rss", "ytdlp"]
    assert "supadata" not in open("sources.yaml").read() + open("people.yaml").read()
    assert sum(s.id.startswith("calls-") for s in sources) >= 10


def test_unknown_method_rejected(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n  - {id: a, name: A, tier: 1, rss: x, transcripts: [magic]}\n")
    with pytest.raises(ValueError):
        load_sources(p)


def test_people_defaults_itunes_only():
    ps = {p.name: p for p in load_people("people.yaml")}
    assert ps["Jensen Huang"].order == ["itunes"] and ps["Hock Tan"].transcripts == ["podcast_transcript", "whisper"]


def test_find_falls_back_in_order(monkeypatch):
    calls = []

    def boom(*a):
        calls.append("rss")
        raise OSError

    monkeypatch.setitem(ingest.FINDERS, "rss", boom)
    monkeypatch.setitem(ingest.FINDERS, "ytdlp", lambda *a: calls.append("ytdlp") or [ep(id="yt:v", youtube_id="v")])
    src = Source("a", "A", 1, rss="x", youtube="https://www.youtube.com/@A/videos", fallback=["ytdlp"],
                 transcripts=["youtube_captions"])
    eps, how = ingest.find(src, NOW, 7)
    assert calls == ["rss", "ytdlp"] and how == "ytdlp" and eps[0].methods == ["youtube_captions"]


def test_youtube_audio_skips_without_whisper(monkeypatch):
    monkeypatch.setattr(whisper, "available", lambda: False)
    assert whisper.transcribe_youtube("v", Budget(600)) == (None, 0.0)
    cmd = whisper.ytdlp_audio_cmd("v", "/tmp/a.%(ext)s")
    assert "bestaudio" in cmd and "--sleep-interval" in cmd and "--limit-rate" in cmd


def test_transcript_method_order_and_budget(monkeypatch):
    import podcast_alpha.whisper as w

    monkeypatch.setattr(transcribe, "from_rss_tag", lambda links: None)
    monkeypatch.setattr(w, "transcribe", lambda url, b: (b.spend(600) or [{"start": 0, "speaker": None, "text": "x"}], 600))
    b = Budget(30, 20)
    segs, label = transcribe.get_transcript(ep(transcript_urls=[{"url": "t"}], audio_url="a",
                                               methods=["podcast_transcript", "whisper"]), b)
    assert label == "whisper (10 min)" and b.used == 600
    assert transcribe.get_transcript(ep(audio_url="a", methods=["whisper"])) == (None, None)


def test_budget_and_segments_until():
    assert Budget(600).take() == 36000
    b = Budget(50, 30)
    assert b.take() == 1800
    b.spend(1800)
    assert b.take() == 1200
    segs = [SimpleNamespace(start=s, text=f" {s} ") for s in (0, 100, 200)]
    assert [s["text"] for s in segments_until(segs, 150)] == ["0", "100"]


def test_feed_transcripts_matches_guid():
    from podcast_alpha.ingest import canonical_id
    entry = {"id": "g1", "podcast_transcript": {"url": "https://x/t.vtt", "type": "text/vtt"}, "links": []}
    e = ep(id=canonical_id("g1", None, None), feed_url="https://feed")
    other = ep(id="rss:none", feed_url="https://feed")
    assert feed_transcripts([e, other], parse=lambda url: SimpleNamespace(entries=[entry])) == 1
    assert e.transcript_urls[0]["url"] == "https://x/t.vtt" and other.transcript_urls is None
