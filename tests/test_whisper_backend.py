import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from podcast_alpha import whisper
from podcast_alpha.triage import triage

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def force_platform(monkeypatch, system, machine, modules):
    monkeypatch.setattr(whisper.sys, "platform", system)
    monkeypatch.setattr(whisper.platform, "machine", lambda: machine)
    monkeypatch.setattr(whisper, "has_module", lambda name: name in modules)


def test_auto_picks_mlx_only_on_apple_silicon_with_mlx(monkeypatch):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "auto")
    force_platform(monkeypatch, "darwin", "arm64", {"mlx_whisper", "faster_whisper"})
    assert whisper.backend() == "mlx" and whisper.available()
    force_platform(monkeypatch, "darwin", "arm64", {"faster_whisper"})
    assert whisper.backend() == "faster" and whisper.available()
    force_platform(monkeypatch, "linux", "x86_64", {"mlx_whisper", "faster_whisper"})
    assert whisper.backend() == "faster"
    force_platform(monkeypatch, "darwin", "x86_64", {"mlx_whisper"})
    assert whisper.backend() == "faster" and not whisper.available()  # Intel Mac: no MLX, and faster is missing
    force_platform(monkeypatch, "darwin", "arm64", {"mlx_whisper"})
    assert whisper.available()  # mlx alone is enough


def test_explicit_backend_and_available(monkeypatch):
    force_platform(monkeypatch, "darwin", "arm64", {"mlx_whisper", "faster_whisper"})
    monkeypatch.setenv("PA_WHISPER_BACKEND", "faster")
    assert whisper.backend() == "faster"
    force_platform(monkeypatch, "linux", "x86_64", {"faster_whisper"})
    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx")
    assert whisper.backend() == "mlx" and not whisper.available()  # asked for mlx, not importable


def test_model_defaults_override_and_label(monkeypatch):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx")
    assert whisper.model_name() == "mlx-community/whisper-large-v3-turbo"
    assert whisper.label(52 * 60) == "whisper large-v3-turbo (52 min)"
    assert whisper.label(600, ", youtube audio") == "whisper large-v3-turbo (10 min, youtube audio)"
    monkeypatch.setenv("PA_WHISPER_BACKEND", "faster")
    assert whisper.model_name() == "base.en" and whisper.label(120) == "whisper base.en (2 min)"
    monkeypatch.setenv("PA_WHISPER_MODEL", "mlx-community/whisper-small.en-mlx")
    assert whisper.model_name() == "mlx-community/whisper-small.en-mlx" and whisper.short_model() == "small.en-mlx"


def test_threads_default_and_env(monkeypatch):
    monkeypatch.setattr(whisper.os, "cpu_count", lambda: 14)
    assert whisper.threads() == whisper.DEFAULT_THREADS
    monkeypatch.setenv("PA_WHISPER_THREADS", "9")
    assert whisper.threads() == 9


def test_mlx_run_respects_budget_and_shape(monkeypatch):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx")
    segs = [{"start": 0.0, "end": 30.0, "text": " one "}, {"start": 90.0, "end": 120.0, "text": "two"},
            {"start": 200.0, "end": 230.0, "text": "three"}]
    seen = {}

    def fake_transcribe(path, **kw):
        seen.update(kw)
        return {"segments": segs}

    monkeypatch.setitem(__import__("sys").modules, "mlx_whisper", SimpleNamespace(transcribe=fake_transcribe))
    monkeypatch.setattr(whisper, "audio_seconds", lambda p: 230.0)
    b = whisper.Budget(2)  # 120 s
    out, used = whisper.transcribe_file("x.mp3", b)
    assert out == [{"start": 0.0, "speaker": None, "text": "one"}, {"start": 90.0, "speaker": None, "text": "two"}]
    assert used == 120 and b.used == 120
    assert seen["language"] == "en" and seen["condition_on_previous_text"] is False
    assert seen["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    monkeypatch.setattr(whisper, "audio_seconds", lambda p: None)  # no ffprobe: fall back to the last segment end
    assert whisper.transcribe_file("x.mp3", whisper.Budget(60))[1] == 230.0


def test_prefetch_failure_is_the_download_error(monkeypatch):
    err = whisper.DownloadError("HTTPError 404", 1, ["HTTPError 404"], False)

    def boom(url, f, **kw):
        raise err

    monkeypatch.setattr(whisper, "download", boom)
    pf = whisper.Prefetch("https://x/a.mp3")
    with pytest.raises(whisper.DownloadError) as ei:
        whisper.transcribe_prefetched(pf, whisper.Budget(60))
    assert ei.value is err and not __import__("os").path.exists(pf.path)  # temp file cleaned up


def test_prefetch_success_transcribes_and_cleans_up(monkeypatch):
    monkeypatch.setattr(whisper, "download", lambda url, f, **kw: f.write(b"audio"))
    monkeypatch.setattr(whisper, "transcribe_file", lambda path, b: (open(path, "rb").read() == b"audio" and [{"start": 0}], 5.0))
    pf = whisper.Prefetch("https://x/a.mp3")
    assert whisper.transcribe_prefetched(pf, whisper.Budget(60)) == ([{"start": 0}], 5.0)
    assert not __import__("os").path.exists(pf.path)


def row(i, title, tier, **kw):
    return {"episode_id": i, "title": title, "source": f"Show {i}", "source_tag": "", "tier": tier, "audio_url": "https://x/a.mp3",
            "has_transcript": False, "published": "2026-10-22", **kw}


def test_tier1_floor_adds_non_news_non_rerun_tier1():
    def manifest():
        return {"episodes": [row("a", "Top pick CEO interview on AI chips", 1), row("b", "Plain chat", 1),
                             row("n", "Market wrap", 1, news=True), row("r", "Replay", 1, rerun=True),
                             row("t2", "Other chat", 2), row("t3", "Another chat", 3)]}

    is_news = lambda e: e.get("news", False)
    m = manifest()
    ids = triage(m, [], top=1, now=NOW, is_news=is_news)
    assert set(ids) == {"a", "b"}
    by = {e["episode_id"]: e["triage"] for e in m["episodes"]}
    assert "tier-1 floor" in by["b"]["reasons"] and "tier-1 floor" not in by["a"]["reasons"]
    assert by["n"]["whisper"] is False and by["r"]["whisper"] is False and by["t2"]["whisper"] is False
    assert m["triage"]["tier1_floor"] is True
    m = manifest()
    assert triage(m, [], top=1, now=NOW, is_news=is_news, tier1_floor=False) == ["a"]
    assert json.dumps(m["triage"]["tier1_floor"]) == "false"


def _mlx_fakes(monkeypatch, duration):
    import os

    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx")
    seen = {"paths": [], "trim": []}

    def fake_transcribe(path, **kw):
        seen["paths"].append(path)
        return {"segments": [{"start": 0.0, "end": 5.0, "text": "hi"}]}

    def fake_trim(path, limit):
        seen["trim"].append((path, limit))
        return "/tmp/trimmed.wav"

    monkeypatch.setitem(__import__("sys").modules, "mlx_whisper", SimpleNamespace(transcribe=fake_transcribe))
    monkeypatch.setattr(whisper, "audio_seconds", lambda p: duration)
    monkeypatch.setattr(whisper, "trim_audio", fake_trim)
    monkeypatch.setattr(whisper.os, "unlink", lambda p: seen.setdefault("unlinked", []).append(p))
    return seen


def test_mlx_trims_audio_when_budget_is_shorter(monkeypatch):
    seen = _mlx_fakes(monkeypatch, 3 * 3600)
    out, used = whisper.transcribe_file("long.mp3", whisper.Budget(5))
    assert seen["trim"] == [("long.mp3", 300)] and seen["paths"] == ["/tmp/trimmed.wav"]
    assert seen["unlinked"] == ["/tmp/trimmed.wav"] and used == 300 and out


def test_mlx_does_not_trim_when_budget_covers_the_audio(monkeypatch):
    seen = _mlx_fakes(monkeypatch, 600)
    whisper.transcribe_file("short.mp3", whisper.Budget(20))
    assert seen["trim"] == [] and seen["paths"] == ["short.mp3"]


def test_mlx_falls_back_to_full_file_when_trim_fails(monkeypatch):
    seen = _mlx_fakes(monkeypatch, 3600)
    monkeypatch.setattr(whisper, "trim_audio", lambda p, l: None)
    whisper.transcribe_file("long.mp3", whisper.Budget(5))
    assert seen["paths"] == ["long.mp3"]
