import json
import os
import time
from datetime import datetime, timezone

import jsonschema
import pytest

from podcast_alpha import doctor, status
from podcast_alpha.cli import main, today_name

SCHEMA = json.loads((__import__("pathlib").Path(__file__).parent.parent / "schema" / "status.schema.json").read_text())


def ep(i, has=False, **kw):
    return {"episode_id": i, "title": f"Title {i}", "source": "Show", "has_transcript": has, "audio_url": "https://x/a.mp3",
            "duration": 1800, "file": f"{i}.md", **kw}


def mk(tmp_path, eps=None, name="2026-10-23", **extra):
    run = tmp_path / name
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"episodes": eps if eps is not None else [ep("a", True), ep("b", True), ep("c", True)], **extra}))
    return run


def info(run, **kw):
    d = status.inspect_run(run, **kw)
    jsonschema.validate(d, SCHEMA)
    return d


def selected(run, links=False):
    eps = [{"episode_id": i, "title": "T", "show": "S", "url": "https://x", "guests": [{"name": f"Guest {i}"}], "kind": "exec",
            "why": "new", **({"listen_url": "https://podcasts.apple.com/x"} if links else {})} for i in "abc"]
    (run / "selected.json").write_text(json.dumps({"date": "2026-10-23", "run_dir": str(run), "selected_by": "t", "episodes": eps}))


def test_empty_run(tmp_path):
    d = info(tmp_path / "2026-10-23")
    assert d["exists"] is False and d["stage"] == "fetch" and d["next"]["type"] == "command"
    assert d["next"]["command"] == "podcast-alpha daily --hours 48 --run-name 2026-10-23"
    assert not (tmp_path / "2026-10-23").exists()  # status never creates anything


def test_transcribe_running_blocks(tmp_path):
    run = mk(tmp_path, [ep("a", True), ep("b"), ep("c")], timing={"transcribe": {"start": "2026-10-23T10:00:00+00:00"}})
    (run / "transcribe.lock").write_text(str(os.getpid()))
    d = info(run)
    assert d["stage"] == "transcribe" and d["next"]["type"] == "wait" and d["stages"]["transcribe"] == "running"
    assert "1/3" in d["blockers"][0] and "60 min" in d["blockers"][0]


def test_transcribe_interrupted_and_not_started(tmp_path):
    run = mk(tmp_path, [ep("a"), ep("b", True)], timing={"transcribe": {"start": "2026-10-23T10:00:00+00:00"}})
    (run / "transcribe.lock").write_text("999999999")  # dead pid
    d = info(run)
    assert d["stages"]["transcribe"] == "failed" and d["next"]["type"] == "command" and "transcribe" in d["next"]["command"]
    d = info(mk(tmp_path, [ep("a")], name="x"))
    assert d["stages"]["transcribe"] == "pending" and d["transcribe"]["state"] == "not_started"


def test_select_then_validation_then_links_then_report(tmp_path):
    run = mk(tmp_path, timing={"transcribe": {"start": "2026-10-23T10:00:00+00:00", "end": "2026-10-23T11:00:00+00:00"}})
    d = info(run)
    assert d["stage"] == "select" and d["next"]["prompt"] == "prompts/select.md" and d["stages"]["transcribe"] == "done"
    (run / "selected.json").write_text("{}")
    d = info(run)
    assert d["stage"] == "select" and d["blockers"]
    selected(run)
    d = info(run)
    assert d["stage"] == "links" and d["stages"]["select"] == "done" and d["next"]["command"].startswith("podcast-alpha links")
    selected(run, links=True)
    d = info(run)
    assert d["stage"] == "report" and d["next"]["prompt"] == "prompts/report.md" and d["stages"]["links"] == "done"


def test_selected_errors_come_from_check(tmp_path):
    run = mk(tmp_path, [ep("a", True), ep("b", True)], timing={"transcribe": {"end": "2026-10-23T11:00:00+00:00"}})  # c not in manifest
    selected(run)
    d = info(run)
    assert d["stage"] == "select" and any("not in manifest" in b for b in d["blockers"])


def test_render_publish_done(tmp_path, monkeypatch):
    run = mk(tmp_path, timing={"transcribe": {"end": "2026-10-23T11:00:00+00:00"}})
    selected(run, links=True)
    (run / "report.json").write_text("{}")
    monkeypatch.setattr(status, "_check", lambda kind, p, r: ({"episodes": [{"episode_id": i, "listen_url": "u"} for i in "abc"]}, []))
    d = info(run)
    assert d["stage"] == "render" and d["next"]["command"].startswith("podcast-alpha report")
    (run / "report.md").write_text("x")
    os.utime(run / "report.md", (time.time() + 5, time.time() + 5))
    d = info(run, env={})
    assert d["stage"] == "publish" and "mark publish end" in d["next"]["command"]
    assert info(run, env={"NOTION_TOKEN": "x"})["stage"] == "done"
    m = json.loads((run / "manifest.json").read_text())
    m["timing"]["publish"] = {"end": "2026-10-23T12:00:00+00:00"}
    (run / "manifest.json").write_text(json.dumps(m))
    d = info(run, env={})
    assert d["stage"] == "done" and d["next"]["type"] == "done"


def test_weekly_flow(tmp_path):
    run = mk(tmp_path, name="weekly-1", days=7)
    assert info(run)["next"]["command"] == f"podcast-alpha rank-prep {run}"
    (run / "rank").mkdir()
    for n in (1, 2):
        (run / "rank" / f"batch-{n}-in.json").write_text("{}")
    (run / "rank" / "batch-1-out.json").write_text("{}")
    d = info(run)
    assert d["kind"] == "weekly" and d["stage"] == "readers" and "batch-2-in.json" in d["next"]["summary"] and d["next"]["prompt"] == "prompts/rank.md"
    (run / "rank" / "batch-2-out.json").write_text("{}")
    assert info(run)["next"]["command"] == f"podcast-alpha rank-merge {run}"
    (run / "ranked.json").write_text("{}")
    d = info(run)
    assert d["stage"] == "rank_merge" and d["blockers"]  # invalid ranked.json is a blocker


def test_record_boundaries_once(tmp_path):
    run = mk(tmp_path, timing={"transcribe": {"start": "2026-10-23T10:00:00+00:00", "end": "2026-10-23T11:00:00+00:00"}})
    selected(run, links=True)
    d = info(run)
    assert status.record_boundaries(run, d) == ["select start", "select end", "report start"]
    assert status.record_boundaries(run, info(run)) == []
    t = json.loads((run / "manifest.json").read_text())["timing"]
    assert "start" in t["select"] and "end" in t["select"] and "start" in t["report"]


def test_cli_status_json_and_today(tmp_path, capsys, monkeypatch):
    run = mk(tmp_path, timing={"transcribe": {"end": "2026-10-23T11:00:00+00:00"}})
    main(["status", str(run), "--json"])
    d = json.loads(capsys.readouterr().out)
    jsonschema.validate(d, SCHEMA)
    assert d["stage"] == "select"
    main(["status", str(run)])
    assert "next (agent)" in capsys.readouterr().out
    # today: existing run prints status, never calls daily
    monkeypatch.setattr("podcast_alpha.cli.today_name", lambda now=None: "2026-10-23")
    monkeypatch.setattr("podcast_alpha.cli.daily", lambda a: (_ for _ in ()).throw(AssertionError("daily called")))
    main(["today", "--runs", str(tmp_path)])
    assert "stage: select" in capsys.readouterr().out


def test_today_starts_daily_when_missing(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_daily(a):
        calls.append((a.run_name, a.hours, a.runs))
        mk(tmp_path, [ep("a")], name=a.run_name)

    monkeypatch.setattr("podcast_alpha.cli.daily", fake_daily)
    main(["today", "--hours", "24", "--run-name", "2026-10-24", "--runs", str(tmp_path)])
    assert calls == [("2026-10-24", 24.0, str(tmp_path))]
    assert "[status]" in capsys.readouterr().out


def test_today_name_is_local_date():
    tz = timezone.utc
    assert today_name(datetime(2026, 10, 23, 23, 30, tzinfo=tz)) == "2026-10-23"


def test_doctor(monkeypatch, capsys):
    rows = doctor.run_checks([("a", lambda: ("ok", "fine")), ("b", lambda: ("warn", "meh")),
                              ("c", lambda: (_ for _ in ()).throw(RuntimeError("boom")))])
    assert [r[0] for r in rows] == ["ok", "warn", "fail"] and "boom" in rows[2][2]
    monkeypatch.setattr(doctor, "CHECKS", [("a", lambda: ("ok", "fine")), ("b", lambda: ("warn", "meh"))])
    main(["doctor"])
    assert "1 ok, 1 warn, 0 fail" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "CHECKS", [("c", lambda: ("fail", "bad"))])
    with pytest.raises(SystemExit) as e:
        main(["doctor"])
    assert e.value.code == 1


def test_doctor_notion_and_whisper(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert doctor.check_notion()[0] == "warn"
    monkeypatch.setenv("NOTION_TOKEN", "x")
    assert doctor.check_notion()[0] == "ok"
    monkeypatch.setattr("podcast_alpha.whisper.available", lambda: False)
    assert doctor.check_whisper()[0] == "warn"


def test_restart_command_uses_pinned_interpreter_and_appends(tmp_path, monkeypatch):
    monkeypatch.setattr("podcast_alpha.fetch.whisper_python", lambda: "/opt/pa venv/bin/python")
    d = info(mk(tmp_path, [ep("a")], name="x"))
    cmd = d["next"]["command"]
    assert cmd.startswith("nohup '/opt/pa venv/bin/python' -m podcast_alpha.cli transcribe ")
    assert " >> " in cmd and " > " not in cmd.replace(" >> ", "")


def test_just_queued_transcribe_is_starting_not_restartable(tmp_path):
    run = mk(tmp_path, [ep("a")])
    (run / "transcribe.queued").write_text(str(os.getpid()))
    d = info(run)
    assert d["transcribe"]["state"] == "starting" and d["next"]["type"] == "wait"
    (run / "transcribe.queued").write_text("999999999")  # child died before taking the lock
    assert info(run)["transcribe"]["state"] == "not_started"


def test_fresh_log_without_lock_is_starting(tmp_path):
    run = mk(tmp_path, [ep("a")])
    (run / "transcribe.log").write_text("x")
    assert info(run)["next"]["type"] == "wait"
    old = time.time() - 3600
    os.utime(run / "transcribe.log", (old, old))
    assert info(run)["next"]["type"] == "command"


def test_queue_transcribe_writes_marker(tmp_path, monkeypatch):
    from podcast_alpha import fetch

    class P:
        pid = 4242

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: P())
    assert fetch.queue_transcribe(tmp_path, 10, None, python="py") == 4242
    assert (tmp_path / "transcribe.queued").read_text() == "4242"


def test_record_boundaries_never_negative_when_selected_predates_first_look(tmp_path):
    run = mk(tmp_path, timing={"transcribe": {"start": "2026-10-23T10:00:00+00:00", "end": "2026-10-23T11:00:00+00:00"}})
    selected(run, links=True)
    old = time.time() - 3600
    os.utime(run / "selected.json", (old, old))
    status.record_boundaries(run, info(run))
    t = json.loads((run / "manifest.json").read_text())["timing"]
    assert t["select"]["seconds"] >= 0 and t["select"]["start"] <= t["select"]["end"]
