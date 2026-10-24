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

    monkeypatch.setattr(transcribe, "from_rss_tag", lambda links, used=None: None)
    monkeypatch.setattr(w, "transcribe", lambda url, b: (b.spend(600) or [{"start": 0, "speaker": None, "text": "x"}], 600))
    b = Budget(30, 20)
    segs, label = transcribe.get_transcript(ep(transcript_urls=[{"url": "t"}], audio_url="a",
                                               methods=["podcast_transcript", "whisper"]), b)
    assert label == "whisper base.en (10 min)" and b.used == 600
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


def test_page_url_falls_back_to_guid_then_links_then_feed():
    audio = "https://traffic.megaphone.fm/PDP1.mp3"
    show = "https://show.example"
    # item has no link: an http(s) guid that is a page wins over the show page
    assert ingest.page_url({"id": "https://show.example/ep1"}, audio, show) == "https://show.example/ep1"
    assert ingest.page_url({"guid": "https://show.example/ep2"}, audio, show) == "https://show.example/ep2"
    # non-URL guid, audio guid, or tracking guid is skipped
    assert ingest.page_url({"id": "a1b2c3d4-0000"}, audio, show) == show
    assert ingest.page_url({"id": audio}, audio, show) == show
    assert ingest.page_url({"id": "https://dts.podtrac.com/redirect.mp3/x"}, None, show) == show
    # real item link still beats the guid
    assert ingest.page_url({"link": "https://show.example/real", "id": "https://show.example/guid"}, audio, show) == "https://show.example/real"
    # guid before other links; then links (skipping audio) before feed link
    entry = {"id": "not a url", "links": [{"rel": "enclosure", "href": audio, "type": "audio/mpeg"},
                                          {"rel": "alternate", "href": "https://show.example/alt"}]}
    assert ingest.page_url(entry, audio, show) == "https://show.example/alt"
    assert ingest.page_url({**entry, "id": "https://show.example/g"}, audio, show) == "https://show.example/g"
    # nothing usable and no feed link: empty, never invented
    assert ingest.page_url({"id": "urn:uuid:1"}, audio, None) == ""


def test_feed_transcripts_loads_each_feed_once_and_survives_a_bad_one():
    from podcast_alpha.ingest import canonical_id
    entry = {"id": "g1", "podcast_transcript": {"url": "https://x/t.vtt", "type": "text/vtt"}, "links": []}
    calls = []

    def parse(url):
        calls.append(url)
        if url == "https://bad":
            raise OSError("down")
        return SimpleNamespace(entries=[entry])

    eps = [ep(id=canonical_id("g1", None, None), feed_url=u) for u in ("https://feed", "https://feed", "https://bad")]
    assert feed_transcripts(eps, parse=parse) == 2
    assert sorted(calls) == ["https://bad", "https://feed"] and eps[2].transcript_urls is None


def test_itunes_backs_off_on_429_and_gives_up_after_retries():
    from datetime import datetime, timezone
    from podcast_alpha.people import ITUNES_RETRIES, from_itunes

    p, now, slept = Person("Jane Doe", "CEO", "core"), datetime(2026, 10, 23, tzinfo=timezone.utc), []
    r429 = SimpleNamespace(status_code=429, headers={"Retry-After": "3"}, raise_for_status=lambda: (_ for _ in ()).throw(OSError("429")))
    ok = SimpleNamespace(status_code=200, headers={}, raise_for_status=lambda: None, json=lambda: {"results": []})
    seq = [r429, r429, ok]
    assert from_itunes(p, now, 2, get=lambda *a, **k: seq.pop(0), sleep=slept.append) == [] and slept == [3.0, 3.0]
    calls = []
    with pytest.raises(OSError):
        from_itunes(p, now, 2, get=lambda *a, **k: calls.append(1) or r429, sleep=lambda s: None)
    assert len(calls) == ITUNES_RETRIES


def test_discover_people_parallel_keeps_person_order_and_records_errors(monkeypatch):
    import time
    from datetime import datetime, timezone
    from podcast_alpha import people as pm

    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    ps = [Person(n, "x", "core") for n in ("A", "B", "C", "D")]

    def fake(p, now_, days):
        time.sleep({"A": 0.15, "B": 0.0}.get(p.name, 0.03))
        if p.name == "C":
            raise RuntimeError("down")
        return [ep(id=f"rss:{p.name}", title={"A": "Zebra economics", "B": "Quantum chips", "D": "Bond auction fallout"}.get(p.name, "x"), source_id=p.tag)]

    monkeypatch.setitem(pm.FINDERS, "itunes", fake)
    kept, info = pm.discover_people(ps, now, 2, [])
    assert [e.id for e in kept] == ["rss:A", "rss:B", "rss:D"] and info["errors"] == ["C/itunes: RuntimeError"]
