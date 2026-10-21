from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from podcast_alpha import ingest, people, supadata, transcribe
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
    assert bg2.order == ["rss", "supadata", "ytdlp"] and bg2.transcripts[0] == "podcast_transcript"
    assert Source("a", "A", 1, rss="x", preferred_source="rss", fallback=["supadata"]).order == ["rss"]


def test_unknown_method_rejected(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n  - {id: a, name: A, tier: 1, rss: x, transcripts: [magic]}\n")
    with pytest.raises(ValueError):
        load_sources(p)


def test_people_defaults_and_youtube_only_for_core():
    ps = {p.name: p for p in load_people("people.yaml")}
    assert ps["Jensen Huang"].order == ["itunes", "supadata"]
    assert ps["Hock Tan"].order == ["itunes"]


def test_find_falls_back_in_order(monkeypatch):
    calls = []

    def boom(*a):
        calls.append("rss")
        raise OSError

    monkeypatch.setitem(ingest.FINDERS, "rss", boom)
    monkeypatch.setitem(ingest.FINDERS, "supadata", lambda *a: calls.append("supadata") or [ep(id="yt:v", youtube_id="v")])
    src = Source("a", "A", 1, rss="x", youtube="https://www.youtube.com/@A/videos", fallback=["supadata", "ytdlp"],
                 transcripts=["supadata"])
    eps, how = ingest.find(src, NOW, 7)
    assert calls == ["rss", "supadata"] and how == "supadata" and eps[0].methods == ["supadata"]


def test_supadata_skips_without_key(monkeypatch):
    monkeypatch.delenv(supadata.ENV, raising=False)
    with pytest.raises(LookupError):
        ingest.from_supadata(Source("a", "A", 1, youtube="https://www.youtube.com/@A"), NOW, 7)
    with pytest.raises(LookupError):
        people.from_supadata_search(Person("A B", "r", "core"), NOW, 7)
    assert transcribe.get_transcript(ep(youtube_id="v", methods=["supadata"])) == (None, None)


def test_discover_people_reports_skipped_supadata(monkeypatch):
    monkeypatch.delenv(supadata.ENV, raising=False)
    monkeypatch.setitem(people.FINDERS, "itunes", lambda p, now, days: [])
    _, meta = people.discover_people([Person("A B", "r", "core", fallback=["supadata"])], NOW, 7, [])
    assert any(s.startswith("supadata") for s in meta["skipped_sources"])


def test_supadata_parsing():
    assert supadata.upload_filter(7) == "week" and supadata.upload_filter(1) == "today" and supadata.upload_filter(400) == "all"
    segs = supadata.to_segments({"content": [{"text": " hi ", "offset": 61000}, {"text": ""}]})
    assert segs == [{"start": 61.0, "speaker": None, "text": "hi"}]
    assert supadata.to_segments({"content": "plain"}) is None


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
