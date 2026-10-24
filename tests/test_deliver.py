import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from podcast_alpha import deliver, doctor, whisper
from podcast_alpha.cli import main

AT = datetime(2026, 10, 26, 11, 45, tzinfo=timezone.utc)


def hb(state, minutes_ago=0):
    return {"state": state, "at": (AT - timedelta(minutes=minutes_ago)).isoformat()}


def test_decide():
    assert deliver.decide(None, 90, AT) == "fallback"
    assert deliver.decide(hb("done", 500), 90, AT) == "pull"
    assert deliver.decide(hb("running", 10), 90, AT) == "pull"
    assert deliver.decide(hb("running", 120), 90, AT) == "fallback"
    assert deliver.decide(hb("failed"), 90, AT) == "fallback"
    assert deliver.decide({"state": "running"}, 90, AT) == "fallback"


def test_deliverable_skips_audio_locks_dbs_holdings(tmp_path):
    for n in ["manifest.json", "t.md", "a.mp3", "transcribe.lock", "x.sqlite", "holdings.json", ".env"]:
        (tmp_path / n).write_text("x")
    keep = sorted(p.name for p in tmp_path.iterdir() if deliver.deliverable(p))
    assert keep == ["manifest.json", "t.md"]


def test_backend_alias_and_flag(monkeypatch):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "faster-whisper")
    assert whisper.backend() == "faster"
    monkeypatch.setattr(whisper, "has_module", lambda n: True)
    status, msg = doctor.check_whisper()
    assert status == "ok" and "backend faster" in msg
    monkeypatch.setenv("PA_WHISPER_BACKEND", "mlx")
    assert "backend mlx (mlx-community/whisper-large-v3-turbo)" in doctor.check_whisper()[1]


def test_backend_flag_sets_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PA_WHISPER_BACKEND", "auto")
    monkeypatch.setattr("podcast_alpha.cli.transcribe_run", lambda a: None)
    import podcast_alpha.cli as cli
    monkeypatch.setattr(cli, "transcribe_run", lambda a: None)
    with pytest.raises(SystemExit):
        main(["transcribe", str(tmp_path), "--backend", "bogus"])
    import os
    main(["heartbeat", "--run", "r", "--runs", str(tmp_path), "--state", "running"])
    assert json.loads((tmp_path / "r" / "heartbeat.json").read_text())["state"] == "running"


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A bare origin and a clone standing in for the Mac checkout; deliver.ROOT/WORKTREE point at the clone."""
    env = {**deliver.AUTHOR}
    origin, mac = tmp_path / "origin.git", tmp_path / "mac"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(mac)], check=True)
    (mac / "README").write_text("code")
    subprocess.run(["git", "add", "."], cwd=mac, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=mac, check=True, env={**__import__("os").environ, **env})
    subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=mac, check=True)
    monkeypatch.setattr(deliver, "ROOT", mac)
    monkeypatch.setattr(deliver, "WORKTREE", mac / ".runs-worktree")
    return origin, mac


def test_deliver_then_pull_roundtrip(repos, tmp_path):
    origin, mac = repos
    run = mac / "runs" / "2026-10-26"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text('{"episodes": []}')
    (run / "ep.md").write_text("transcript")
    (run / "ep.mp3").write_bytes(b"audio")
    deliver.write_heartbeat(run, "done")
    assert deliver.deliver(run) == 3
    (run / "ep2.md").write_text("more")
    assert deliver.deliver(run) == 4  # second delivery reuses the worktree
    main_files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=mac, capture_output=True, text=True).stdout
    assert "runs/" not in main_files  # main untouched
    assert deliver.remote_heartbeat("2026-10-26")["state"] == "done"
    box_runs = tmp_path / "box-runs"
    assert deliver.pull_run("2026-10-26", box_runs) == 4
    assert not (box_runs / "2026-10-26" / "ep.mp3").exists()
    assert (box_runs / "2026-10-26" / "ep2.md").read_text() == "more"


def test_fallback_check_runs_daily_when_missing(repos, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("podcast_alpha.cli.main", lambda argv: calls.append(argv))
    args = SimpleNamespace(run="2026-10-27", runs=str(tmp_path / "r"), stale_minutes=90, dry_run=False, hours=48)
    deliver.fallback_check_cmd(args)
    assert calls and calls[0][0] == "daily" and "2026-10-27" in calls[0]
    calls.clear()
    deliver.fallback_check_cmd(SimpleNamespace(**{**vars(args), "dry_run": True}))
    assert not calls


def test_git_follows_patched_root(repos):
    _, mac = repos
    top = deliver.git("rev-parse", "--show-toplevel").stdout.strip()
    assert Path(top).resolve() == mac.resolve()
