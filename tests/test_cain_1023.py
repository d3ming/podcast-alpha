"""10-23 feedback: download retry, exec/clip triage, idle in stats, check inference, show-notes YouTube."""
import io
import json

import pytest
import requests

from podcast_alpha import fetch as fetch_mod, links, timing, triage as tri, whisper
from podcast_alpha.cli import infer_kind, main
from podcast_alpha.ingest import parse_duration
from podcast_alpha.people import Person


class Resp:
    def __init__(self, code, body=b"audio"):
        self.status_code, self.body = code, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, n):
        yield self.body


def seq_get(*codes):
    it = iter(codes)

    def get(url, **kw):
        c = next(it)
        if isinstance(c, Exception):
            raise c
        return Resp(c)
    return get


def test_download_retries_transient_then_succeeds():
    sleeps, f = [], io.BytesIO()
    n = whisper.download("u", f, retries=3, backoff=1, sleep=sleeps.append,
                         get=seq_get(503, requests.ConnectionError("x"), 429, 200))
    assert n == 4 and sleeps == [1, 2, 4] and f.getvalue() == b"audio"


def test_download_no_retry_on_404():
    sleeps = []
    with pytest.raises(whisper.DownloadError) as ei:
        whisper.download("u", io.BytesIO(), retries=3, backoff=1, sleep=sleeps.append, get=seq_get(404))
    assert ei.value.attempts == 1 and not ei.value.transient and sleeps == [] and "404" in str(ei.value)


def test_download_gives_up_after_retries():
    with pytest.raises(whisper.DownloadError) as ei:
        whisper.download("u", io.BytesIO(), retries=2, backoff=0, sleep=lambda s: None, get=seq_get(500, 502, 504))
    assert ei.value.attempts == 3 and ei.value.errors == ["HTTPError 500", "HTTPError 502", "HTTPError 504"]


def test_transcribe_run_logs_failure(monkeypatch, tmp_path):
    from tests.test_ops import ep, make_run, targs

    run = make_run(tmp_path / "r", [ep("rss:a", "Founder interview on AI chips")])
    monkeypatch.setattr(whisper, "available", lambda: True)

    def boom(url, b):
        raise whisper.DownloadError("HTTPError 503", 4, ["HTTPError 503"] * 4, True)
    monkeypatch.setattr(whisper, "transcribe", boom)
    fetch_mod.transcribe_run(targs(run, top=0, people=None))
    m = json.loads((run / "manifest.json").read_text())
    assert m["failures"][0]["episode_id"] == "rss:a" and m["failures"][0]["attempts"] == 4
    assert m["episodes"][0]["whisper_error"] == "HTTPError 503"
    assert json.loads((run / "transcribe.failed.log").read_text().splitlines()[0])["transient"] is True


def E(i, title, tier=1, desc="", tag="", dur=None, source=None):
    return {"episode_id": i, "title": title, "tier": tier, "description": desc, "source_tag": tag, "duration": dur,
            "source": source or f"show-{i}", "audio_url": f"https://a/{i}.mp3", "published": "2026-10-22"}


PEOPLE = [Person(name="Chamath Palihapitiya", priority="core", role="x"), Person(name="Sam Altman", priority="core", role="x")]


def test_exec_guest_on_tier1_beats_offdomain_person_hit_and_clip_dropped():
    from datetime import datetime, timezone

    m = {"episodes": [
        E("exec", "Democratizing Security with Cloudflare CTO Dane Knecht", tier=1),
        E("inv", "Stan Druckenmiller: Macro, QE, Inflation", tier=2),
        E("psy", "She Psychically Read Sam Altman", tier=2, tag="person:Sam Altman", desc="a mystic reads souls"),
        E("full", "Chamath: AGI Is Here, Anthropic's Giveaway", tier=2, tag="person:Chamath Palihapitiya", dur=7300),
        E("clip", "AGI Is Already Here | Chamath Palihapitiya", tier=2, tag="person:Chamath Palihapitiya", dur=3100),
        E("meh", "Random chat", tier=3)]}
    ids = tri.triage(m, PEOPLE, top=3, now=datetime(2026, 10, 23, tzinfo=timezone.utc))
    by = {e["episode_id"]: e["triage"] for e in m["episodes"]}
    assert m["triage"]["clips"] == {"clip": "full"}
    assert "clip" not in ids and "psy" not in ids  # neither top-N nor the core override
    assert {"exec", "inv"} <= set(ids)
    assert any(r.startswith("off-domain") for r in by["psy"]["reasons"])
    assert any("tier-1" in r for r in by["exec"]["reasons"])


def test_clip_needs_shared_guest_and_short_duration():
    idx = tri.person_index(PEOPLE)
    a = E("a", "Chamath on AGI", tag="person:Chamath Palihapitiya", dur=3000, source="X")
    b = E("b", "Chamath on AGI and power", tag="person:Chamath Palihapitiya", dur=3200, source="Y")
    assert tri.find_clips([a, b], idx) == {}  # similar length: not a clip


def test_parse_duration():
    assert parse_duration("1:02:03") == 3723 and parse_duration("45:00") == 2700 and parse_duration("600") == 600
    assert parse_duration(None) is None and parse_duration("abc") is None


def test_stats_splits_idle_from_active(tmp_path):
    run = tmp_path / "2026-10-23"
    run.mkdir()
    t = {}
    m = {"episodes": []}
    for st, a, b in [("fetch", "04:00", "04:05"), ("transcribe", "04:05", "04:15"), ("select", "09:00", "09:03"),
                     ("report", "09:03", "09:05")]:
        timing.apply(m, st, "start", f"2026-10-23T{a}:00+00:00")
        timing.apply(m, st, "end", f"2026-10-23T{b}:00+00:00")
    assert m["timing"]["e2e"]["active_seconds"] == 20 * 60 and m["timing"]["e2e"]["idle_seconds"] == 4 * 3600 + 45 * 60
    assert m["timing"]["e2e"]["seconds"] == 5 * 3600 + 5 * 60
    (run / "manifest.json").write_text(json.dumps(m))
    out = timing.stats(tmp_path)
    assert "20m00s" in out and "4h45m" in out and "5h05m" in out


def test_check_infers_kind(tmp_path, capsys):
    assert infer_kind("runs/x/selected.json") == "selected" and infer_kind("report.json") == "report"
    with pytest.raises(SystemExit):
        infer_kind("manifest.json")
    with pytest.raises(SystemExit):
        main(["check", str(tmp_path / "foo.json")])


def test_youtube_from_show_notes_first(monkeypatch):
    calls = []

    def fetch(url, *a, **k):
        calls.append(url)
        if "oembed" in url and "AAAAAAAAAAA" in url:
            return json.dumps({"title": "Other episode entirely", "author_name": "Show"})
        if "oembed" in url and "BBBBBBBBBBB" in url:
            return json.dumps({"title": "Jensen Huang Thinks A.I. Alarmism Has Gone Too Far", "author_name": "The Ezra Klein Show"})
        raise AssertionError("should not reach channel RSS or search: " + url)
    ep = {"title": "Jensen Huang Thinks A.I. Alarmism Has Gone Too Far", "show": "The Ezra Klein Show",
          "description": "Prev: https://youtu.be/AAAAAAAAAAA Watch: https://www.youtube.com/watch?v=BBBBBBBBBBB&t=1"}
    assert links.youtube(ep, "UCchan", fetch) == "https://www.youtube.com/watch?v=BBBBBBBBBBB"
    assert not any("feeds/videos.xml" in c or "duckduckgo" in c for c in calls)
