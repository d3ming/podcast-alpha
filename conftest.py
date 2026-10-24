"""Repo root on sys.path (so `from tests.x import ...` works under plain `pytest`); tests never pick a real whisper backend."""
import pytest


@pytest.fixture(autouse=True)
def _whisper_env(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "faster")
    monkeypatch.delenv("PA_WHISPER_MODEL", raising=False)
    monkeypatch.delenv("PA_WHISPER_THREADS", raising=False)
    monkeypatch.setenv("PA_NO_LIBRARY", "1")  # report/publish must not ingest test runs into the real data/library.sqlite
    monkeypatch.setenv("PA_NO_PUBLISHED", "1")  # ...or export them into the committed published/ folder
    monkeypatch.setenv("PA_FEED_CACHE", str(tmp_path_factory.mktemp("feed_cache")))  # never write the real data/feed_cache
    from podcast_alpha import whisper

    monkeypatch.setattr(whisper, "PREFETCH", False)  # fake URLs in tests must not be downloaded in the background
