import json
import os
from dataclasses import replace

from podcast_alpha import fetch as fetch_mod
from podcast_alpha import whisper
from tests.test_runtime import LONG, ep, make_run, targs


def rows(run):
    return {e["episode_id"]: e for e in json.loads((run / "manifest.json").read_text())["episodes"]}


def three(tmp_path):
    return make_run(tmp_path / "r", [replace(ep(f"rss:{c}", t), tier=2) for c, t in
                                     (("a", "Founder interview on AI chips"), ("b", "CEO on guidance"), ("c", "Fed and inflation"))])


def test_loop_prefetches_next_episode_and_cleans_up(monkeypatch, tmp_path):
    run = three(tmp_path)
    monkeypatch.setattr(whisper, "PREFETCH", True)
    monkeypatch.setattr(whisper, "available", lambda: True)
    downloads, files = [], []
    monkeypatch.setattr(whisper, "download", lambda url, f, **kw: downloads.append(url) or f.write(b"x"))

    def fake_file(path, budget):
        files.append(path)
        budget.spend(180)
        return LONG, 180.0

    monkeypatch.setattr(whisper, "transcribe_file", fake_file)
    fetch_mod.transcribe_run(targs(run, top=0, people=None))
    m = rows(run)
    assert all(e["has_transcript"] and e["transcript_source"] == "whisper base.en (3 min)" for e in m.values())
    assert sorted(downloads) == sorted(f"https://cdn.example/rss:{c}.mp3" for c in "abc")  # each episode downloaded once
    assert len(files) == 3 and not any(os.path.exists(f) for f in files)


def test_failed_prefetch_is_recorded_like_a_failed_download(monkeypatch, tmp_path):
    run = three(tmp_path)
    monkeypatch.setattr(whisper, "PREFETCH", True)
    monkeypatch.setattr(whisper, "available", lambda: True)

    def download(url, f, **kw):
        if url.endswith("rss:b.mp3"):
            raise whisper.DownloadError("HTTPError 404", 1, ["HTTPError 404"], False)
        f.write(b"x")

    monkeypatch.setattr(whisper, "download", download)
    monkeypatch.setattr(whisper, "transcribe_file", lambda p, b: (b.spend(60) or LONG, 60.0))
    fetch_mod.transcribe_run(targs(run, top=0, people=None))
    man = json.loads((run / "manifest.json").read_text())
    m = rows(run)
    assert m["rss:b"]["whisper_error"] == "HTTPError 404" and not m["rss:b"]["has_transcript"]
    assert m["rss:a"]["has_transcript"] and m["rss:c"]["has_transcript"]
    assert [f["episode_id"] for f in man["failures"]] == ["rss:b"] and man["failures"][0]["transient"] is False
    assert "rss:b" in (run / "transcribe.failed.log").read_text()


def test_no_tier1_floor_flag(monkeypatch, tmp_path):
    run = make_run(tmp_path / "r", [ep("rss:a", "Founder interview on AI chips"), ep("rss:b", "Random chat"), ep("rss:c", "CEO on guidance")])
    monkeypatch.setattr(whisper, "available", lambda: True)
    calls = []
    monkeypatch.setattr(whisper, "transcribe", lambda url, b: calls.append(url) or (LONG, 60.0))
    fetch_mod.transcribe_run(targs(run, top=1, people=None, no_tier1_floor=True))
    assert len(calls) == 1
    run2 = make_run(tmp_path / "other" / "r2", [ep("rss:a", "Founder interview on AI chips"), ep("rss:b", "Random chat"), ep("rss:c", "CEO on guidance")])
    calls.clear()
    fetch_mod.transcribe_run(targs(run2, top=1, people=None))
    assert len(calls) == 3  # all tier 1: the floor whispers them all


def test_has_whisper_follows_backend_availability(monkeypatch):
    import sys

    assert fetch_mod.has_whisper(sys.executable) == whisper.available()  # same check, run inside the candidate interpreter
    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx" if not whisper.has_module("mlx_whisper") else "faster")
    assert fetch_mod.has_whisper(sys.executable) == whisper.available()
