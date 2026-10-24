import json

from podcast_alpha import cli, library


def test_after_stage_ingests_into_library(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PA_NO_LIBRARY")
    monkeypatch.setattr(library, "DEFAULT_DB", tmp_path / "lib.sqlite")
    run = tmp_path / "2026-10-20"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"episodes": [
        {"episode_id": "rss:a", "title": "A", "source": "Show", "published": "2026-10-20", "file": "a.md", "tier": 1}]}))
    cli.after_stage(run, ingest=True)
    assert "[library] 2026-10-20: 0 claims, 1 episodes" in capsys.readouterr().out
    assert (tmp_path / "lib.sqlite").exists()


def test_after_stage_never_fails_the_command(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PA_NO_LIBRARY")
    monkeypatch.setattr(library, "DEFAULT_DB", tmp_path / "lib.sqlite")
    cli.after_stage(tmp_path / "missing-run", ingest=True)  # nothing to ingest: warns, does not raise
    assert "[library] warning: not ingested" in capsys.readouterr().out
