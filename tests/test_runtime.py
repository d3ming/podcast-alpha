import json
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from podcast_alpha import fetch as fetch_mod
from podcast_alpha import whisper
from podcast_alpha.cli import run_date
from podcast_alpha.ingest import Episode, page_url
from podcast_alpha.people import Person, on_topic, parse_itunes

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
LONG = [{"start": i * 10, "speaker": None, "text": "word " * 100} for i in range(20)]


def targs(run, **kw):
    return SimpleNamespace(run=str(run), minutes=600, per_episode=None, db=None, force=False, retry_errors=False,
                           include_reruns=False, min_transcript_chars=5000, skip_news=False, **kw)


def ep(i, title, src="show", audio=None):
    return Episode(id=i, source_id=src, source_name=src.title(), tier=1, title=title, url="u", published_at=NOW,
                   audio_url=audio or f"https://cdn.example/{i}.mp3")


def make_run(d, eps, segs=None, **extra):
    d.mkdir(parents=True)
    rows = []
    for e in eps:
        r = fetch_mod.write_episode(d, e, segs, "whisper (3 min)" if segs else None)
        r.update(extra.get(e.id, {}))
        rows.append(r)
    (d / "manifest.json").write_text(json.dumps({"episodes": rows}))
    return d


def test_whisper_python_prefers_interpreter_with_faster_whisper(monkeypatch, tmp_path):
    monkeypatch.delenv("PA_PYTHON", raising=False)
    monkeypatch.setattr(fetch_mod, "has_whisper", lambda p: p == "/workspace/pa-venv/bin/python")
    monkeypatch.setattr(fetch_mod, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_mod.Path, "exists", lambda self: True)
    assert fetch_mod.whisper_python() == "/workspace/pa-venv/bin/python"
    monkeypatch.setattr(fetch_mod, "has_whisper", lambda p: False)
    with pytest.raises(SystemExit, match="no interpreter with faster_whisper"):
        fetch_mod.whisper_python()


def test_daily_fails_fast_before_fetch(monkeypatch):
    monkeypatch.setattr(fetch_mod, "whisper_python", lambda: (_ for _ in ()).throw(SystemExit("no whisper")))
    monkeypatch.setattr(fetch_mod, "fetch", lambda a: pytest.fail("fetched without a whisper interpreter"))
    with pytest.raises(SystemExit):
        fetch_mod.daily(SimpleNamespace(hours=48, no_transcribe=False))


def test_queue_uses_pinned_python(monkeypatch, tmp_path):
    seen = {}

    class P:
        pid = 7

    monkeypatch.setattr("subprocess.Popen", lambda cmd, **k: seen.setdefault("cmd", cmd) and P())
    assert fetch_mod.queue_transcribe(tmp_path, 600, None, "/venv/bin/python", skip_news=True) == 7
    assert seen["cmd"][0] == "/venv/bin/python" and "--skip-news" in seen["cmd"] and seen["cmd"][-2:] == ["--top", "8"]


def test_transcribe_preflight_and_stale_lock(monkeypatch, tmp_path):
    run = make_run(tmp_path / "r", [])
    monkeypatch.setattr(whisper, "available", lambda: False)
    with pytest.raises(SystemExit, match="faster_whisper is not importable"):
        fetch_mod.transcribe_run(targs(run))
    monkeypatch.setattr(whisper, "available", lambda: True)
    (run / "transcribe.lock").write_text("999999999")
    fetch_mod.transcribe_run(targs(run))  # dead pid: taken over
    (run / "transcribe.lock").write_text(str(__import__("os").getpid()))
    with pytest.raises(SystemExit, match="held by running pid"):
        fetch_mod.transcribe_run(targs(run))


def test_reuse_transcript_from_other_run(monkeypatch, tmp_path):
    make_run(tmp_path / "2026-10-21-daily", [ep("rss:a", "Reducto on documents", "a16z"),
                                            ep("rss:b", "Halftime: AI trade", "halftime", "https://dts.podtrac.com/redirect.mp3/x.com/h1.mp3?t=1")], LONG)
    run = make_run(tmp_path / "2026-10-22", [ep("rss:a", "Reducto on documents", "a16z"),
                                            ep("rss:z", "Halftime: AI trade!", "halftime", "https://x.com/h1.mp3"),
                                            ep("rss:n", "Brand new", "a16z")])
    monkeypatch.setattr(whisper, "available", lambda: True)
    calls = []
    monkeypatch.setattr(whisper, "transcribe", lambda url, b: calls.append(url) or (LONG, 180.0))
    fetch_mod.transcribe_run(targs(run))
    m = {e["episode_id"]: e for e in json.loads((run / "manifest.json").read_text())["episodes"]}
    assert calls == ["https://cdn.example/rss:n.mp3"]
    assert all(e["has_transcript"] for e in m.values())
    assert "[0:00:10] word" in (run / m["rss:z"]["file"]).read_text()


def test_news_whispered_last_or_skipped(monkeypatch, tmp_path):
    run = make_run(tmp_path / "r", [ep("rss:h", "Halftime", "cnbc-halftime"), ep("rss:x", "Deep dive", "no-priors")],
                   **{"rss:h": {"news": True}})
    monkeypatch.setattr(whisper, "available", lambda: True)
    calls = []
    monkeypatch.setattr(whisper, "transcribe", lambda url, b: calls.append(url) or (LONG, 60.0))
    fetch_mod.transcribe_run(targs(run))
    assert calls == ["https://cdn.example/rss:x.mp3", "https://cdn.example/rss:h.mp3"]
    run2 = make_run(tmp_path / "r2", [ep("rss:h2", "Squawk", "squawk-pod")])
    calls.clear()
    a = targs(run2)
    a.skip_news = True
    fetch_mod.transcribe_run(a)
    assert calls == []
    assert fetch_mod.is_news({"source": "CNBC Power Lunch"})


def test_sources_flag_news():
    from podcast_alpha.config import load_sources

    news = {s.id for s in load_sources("sources.yaml")[0] if s.news}
    assert {"cnbc-halftime", "squawk-pod", "cnbc-power-lunch"} <= news and "no-priors" not in news


def test_people_filter_rejects_sports_namesake():
    p = Person("Eric Schmidt", "former Google CEO", "core", context=["Google", "AI"])
    base = {"releaseDate": "2026-10-22T08:00:00Z", "trackTimeMillis": 3_600_000, "episodeGuid": "g"}
    rows = [{**base, "trackName": "FULL SHOW: Kurtiss Riggs (SDSU vs UND vs USD)", "description": "Eric Schmidt joins",
             "collectionName": "Riggs Radio", "genres": [{"name": "Sports", "id": "1545"}]},
            {**base, "trackName": "Drew Trafton, Sam Herder, Eric Schmidt", "description": "FCS football",
             "collectionName": "Hero Sports"},
            {**base, "episodeGuid": "g2", "trackName": "Eric Schmidt on AI and China", "description": "Former Google CEO",
             "collectionName": "Tech Show", "genres": [{"name": "Technology"}]}]
    out = parse_itunes(p, rows, NOW, 2)
    assert [e.title for e in out] == ["Eric Schmidt on AI and China"]
    assert not on_topic(p, "Eric Schmidt talks gardening", "", "Garden Hour")
    assert on_topic(Person("Jane Doe", "", "watch"), "Jane Doe on chips", "", "Chip Pod")


def test_people_yaml_schmidt_has_context():
    from podcast_alpha.people import load_people

    assert next(p for p in load_people("people.yaml") if p.name == "Eric Schmidt").context


def test_page_url_prefers_item_link_over_tracking():
    audio = "https://traffic.megaphone.fm/PDP1.mp3"
    assert page_url({"link": "https://show.example/ep1"}, audio) == "https://show.example/ep1"
    assert page_url({"link": audio}, audio, "https://show.example") == "https://show.example"
    assert page_url({"link": "https://prfx.byspotify.com/e/dts.podtrac.com/redirect.mp3/x"}, None, None) == ""
    assert page_url({"links": [{"rel": "alternate", "href": "https://a.example/p"}, {"type": "audio/mpeg", "href": audio}]},
                    audio) == "https://a.example/p"


class FakeFeed(dict):
    def __init__(self, entries, feed):
        super().__init__(entries=entries, feed=feed)
        self.entries = entries


def test_rss_keeps_enclosure_separately():
    from podcast_alpha.config import Source
    from podcast_alpha.ingest import from_rss

    entry = {"id": "g", "title": "T", "published": "Thu, 22 Oct 2026 08:00:00 GMT", "link": "https://traffic.megaphone.fm/X.mp3",
             "links": [{"type": "audio/mpeg", "href": "https://traffic.megaphone.fm/X.mp3"}]}
    e = from_rss(Source("s", "S", 1, rss="x"), NOW, 2, FakeFeed([entry], {"link": "https://nopriors.example"}))[0]
    assert e.url == "https://nopriors.example" and e.audio_url == "https://traffic.megaphone.fm/X.mp3"


def test_tmtb_date_defaults_to_yesterday(tmp_path, monkeypatch):
    assert run_date("yesterday", date(2026, 10, 22)) == "2026-10-21"
    assert run_date("today", date(2026, 10, 22)) == "2026-10-22"
    assert run_date("2026-10-20") == "2026-10-20"
    from podcast_alpha import cli, tmtb

    (tmp_path / "runs" / run_date("yesterday")).mkdir(parents=True)
    (tmp_path / "runs" / run_date("yesterday") / "manifest.json").write_text('{"episodes": []}')
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    got = {}
    monkeypatch.setattr(tmtb, "run", lambda text, run_dir, days: got.setdefault("dir", run_dir))
    email = tmp_path / "e.txt"
    email.write_text("x")
    cli.main(["tmtb-import", str(email)])
    assert got["dir"].name == run_date("yesterday")


def test_agents_md_uses_installed_command():
    text = open("AGENTS.md").read()
    assert "`pa " not in text and "podcast-alpha daily" in text


def test_norm_audio_strips_trackers_without_collisions():
    a = fetch_mod.norm_audio("https://dts.podtrac.com/redirect.mp3/chtbl.com/track/ABC/cdn.x.com/show/ep1/default.mp3?u=1")
    assert a == "cdn.x.com/show/ep1/default.mp3"
    assert fetch_mod.norm_audio("https://cdn.x.com/show/ep2/default.mp3") != a
