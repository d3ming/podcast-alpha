"""Read-only run inspector: which stage a run folder is in and the single next action for an agent.

`inspect_run` never writes. `record_boundaries` is the only writer (opt-in, `status --record`): it stamps
select start/end and report start/end in manifest.json through the locked `timing.mark`.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DAILY_STAGES = ("fetch", "transcribe", "select", "links", "report", "render", "publish")
WEEKLY_STAGES = ("fetch", "rank_prep", "readers", "rank_merge", "publish")


def _json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: str) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def is_weekly(run: Path, manifest: dict | None) -> bool:
    if (run / "ranked.json").exists() or (run / "rank").is_dir():
        return True
    return bool(manifest) and (manifest.get("days") or 0) > 3 and not (run / "selected.json").exists()


STARTING_LOG_SECONDS = 60


def _starting(run: Path) -> bool:
    """A transcribe child was just queued: transcribe.queued names a live pid, or the log was written seconds ago."""
    q = run / "transcribe.queued"
    if q.exists():
        try:
            return _pid_alive(q.read_text().strip())
        except OSError:
            return False
    log = run / "transcribe.log"
    try:
        return time.time() - log.stat().st_mtime < STARTING_LOG_SECONDS and not (run / "transcribe.lock").exists()
    except OSError:
        return False


def transcribe_state(run: Path, manifest: dict) -> dict:
    eps = manifest.get("episodes", [])
    total = len(eps)
    done = sum(1 for e in eps if e.get("has_transcript"))
    todo = [e for e in eps if not e.get("has_transcript") and e.get("audio_url") and not e.get("whisper_error")
            and not e.get("rerun") and e.get("triage", {}).get("whisper", True)]
    lock, pid = run / "transcribe.lock", None
    if lock.exists():
        pid = lock.read_text().strip()
    t = (manifest.get("timing") or {}).get("transcribe", {})
    running = bool(pid) and _pid_alive(pid)
    if running:
        state = "running"
    elif not t.get("end") and _starting(run):
        state = "starting"  # queued child alive but has not taken the lock yet
    elif t.get("end"):
        state = "finished"
    elif t.get("start") or pid:
        state = "interrupted"  # started, no end, no live process: crashed or killed
    elif not todo:
        state = "finished"  # nothing to whisper
    else:
        state = "not_started"
    errors = sum(1 for e in eps if e.get("whisper_error"))
    audio_min = round(sum(e.get("duration") or 0 for e in todo) / 60)
    return {"state": state, "pid": pid if running else None, "transcribed": done, "total": total,
            "whisper_pending": len(todo), "audio_minutes_pending": audio_min, "errors": errors}


def _check(kind: str, path: Path, run: Path):
    """(data|None, errors) using the same validators as `podcast-alpha check`."""
    from . import ranked, report

    data = _json(path)
    if data is None:
        return None, [f"{path.name} is not valid JSON"]
    try:
        data = report.validate(data, kind)
    except ranked.RankedError as exc:
        return None, [str(exc)]
    return data, report.verify(kind, data, run)


def _links_missing(sel: dict) -> list[str]:
    return [e["episode_id"] for e in sel["episodes"] if not e.get("listen_url")]


def _mtime(p: Path) -> float:
    return p.stat().st_mtime


def inspect_run(run: str | Path, arg: str | None = None, weekly: bool | None = None, env: dict | None = None) -> dict:
    run = Path(run)
    arg = arg or str(run)
    env = os.environ if env is None else env
    manifest = _json(run / "manifest.json") if (run / "manifest.json").exists() else None
    wk = is_weekly(run, manifest) if weekly is None else weekly
    out = {"run": arg, "name": run.name, "kind": "weekly" if wk else "daily", "exists": run.is_dir(),
           "stages": {}, "blockers": [], "notes": []}
    st = out["stages"]
    stages = WEEKLY_STAGES if wk else DAILY_STAGES
    for s in stages:
        st[s] = "pending"

    def finish(stage, kind, summary, command=None, prompt=None):
        out["stage"] = stage
        out["next"] = {k: v for k, v in {"type": kind, "summary": summary, "command": command, "prompt": prompt}.items() if v}
        return out

    if manifest is None:
        out["stages"]["fetch"] = "pending"
        name = run.name
        cmd = (f"podcast-alpha fetch --days 7 --run-name {name}" if wk else
               f"podcast-alpha daily --hours 48 --run-name {name}")
        return finish("fetch", "command", "No manifest.json: fetch the episodes.", cmd)
    st["fetch"] = "done"
    tm = manifest.get("timing") or {}
    errs = [f"{k} ({v['error']})" for k, v in (manifest.get("sources") or {}).items() if v.get("error")]
    if errs:
        out["notes"].append(f"{len(errs)} source error(s) in fetch: {'; '.join(errs[:5])}")
    out["episodes"] = {"total": len(manifest.get("episodes", [])),
                       "transcribed": sum(1 for e in manifest.get("episodes", []) if e.get("has_transcript"))}
    out["timing"] = {k: {kk: vv for kk, vv in tm[k].items() if kk in ("start", "end", "seconds")}
                     for k in ("fetch", "transcribe", "select", "report", "publish") if k in tm}
    return (_weekly(run, arg, manifest, out, finish, env) if wk else _daily(run, arg, manifest, out, finish, env))


def restart_command(arg: str) -> str:
    """Detached restart using the interpreter `daily` pins (bare `podcast-alpha` may be a shim without whisper); appends to the log."""
    try:
        from .fetch import whisper_python

        py = whisper_python()
    except SystemExit:
        py = sys.executable
    return f"nohup {shlex.quote(py)} -m podcast_alpha.cli transcribe {arg} >> {arg}/transcribe.log 2>&1 &"


def _daily(run, arg, manifest, out, finish, env):
    st = out["stages"]
    tr = transcribe_state(run, manifest)
    out["transcribe"] = tr
    if tr["state"] == "running":
        st["transcribe"] = "running"
        out["blockers"].append(f"transcribe still running (pid {tr['pid']}), {tr['transcribed']}/{tr['total']} transcribed, "
                               f"{tr['whisper_pending']} queued, ~{tr['audio_minutes_pending']} min of audio left")
        return finish("transcribe", "wait", "Wait for transcribe to finish, then run status again (check every ~5 min; "
                      f"log: {arg}/transcribe.log).", f"podcast-alpha status {arg}")
    if tr["state"] == "starting":
        st["transcribe"] = "running"
        out["blockers"].append("transcribe was just queued and has not taken its lock yet")
        return finish("transcribe", "wait", "Transcribe is starting: wait, then run status again "
                      f"(log: {arg}/transcribe.log).", f"podcast-alpha status {arg}")
    if tr["state"] in ("not_started", "interrupted"):
        st["transcribe"] = "failed" if tr["state"] == "interrupted" else "pending"
        why = "was interrupted (no live process, no end recorded)" if tr["state"] == "interrupted" else "has not started"
        out["blockers"].append(f"transcribe {why}; {tr['whisper_pending']} episodes to whisper")
        return finish("transcribe", "command", f"Transcribe {why}: (re)start it detached; it is resumable.",
                      restart_command(arg))
    st["transcribe"] = "done"
    if tr["whisper_pending"]:
        out["notes"].append(f"transcribe finished with {tr['whisper_pending']} episodes still untranscribed (budget or triage)")
    if tr["errors"]:
        out["notes"].append(f"{tr['errors']} episode(s) failed to transcribe (whisper_error)")
    out["optional"] = {"seeds": (run / "seeds.json").exists(), "missed": (run / "missed.md").exists()}
    if out["optional"]["missed"]:
        out["notes"].append(f"{arg}/missed.md exists: fetch or whisper anything important in it before selecting")

    sel_p, rep_p, md_p = run / "selected.json", run / "report.json", run / "report.md"
    if not sel_p.exists():
        return finish("select", "agent", "Pick exactly 3 episodes and write selected.json, then run check.",
                      f"podcast-alpha check selected {arg}/selected.json", "prompts/select.md")
    sel, errors = _check("selected", sel_p, run)
    if errors:
        out["blockers"] += errors
        return finish("select", "agent", "selected.json fails check: fix the errors listed under blockers.",
                      f"podcast-alpha check selected {arg}/selected.json", "prompts/select.md")
    st["select"] = "done"
    missing = _links_missing(sel)
    if missing:
        return finish("links", "agent", f"Set youtube_url/youtube_published in selected.json for each pick only on a title-or-guest "
                      f"and date match (web search), then resolve links. {len(missing)} of 3 picks have no listen_url.",
                      f"podcast-alpha links {arg}", "prompts/report.md")
    st["links"] = "done"
    if not rep_p.exists():
        return finish("report", "agent", "Write report.json from the three picks, then render it.",
                      f"podcast-alpha check report {arg}/report.json", "prompts/report.md")
    _, errors = _check("report", rep_p, run)
    if errors:
        out["blockers"] += errors
        return finish("report", "agent", "report.json fails check: fix the errors listed under blockers.",
                      f"podcast-alpha check report {arg}/report.json", "prompts/report.md")
    st["report"] = "done"
    if not md_p.exists() or _mtime(md_p) < _mtime(rep_p):
        return finish("render", "command", "Render report.md (publishes to Notion when NOTION_TOKEN and a parent page are set).",
                      f"podcast-alpha report {arg}/report.json")
    st["render"] = "done"
    pub = (manifest.get("timing") or {}).get("publish", {})
    if pub.get("end"):
        st["publish"] = "done"
        return finish("done", "done", f"Finished. Report: {arg}/report.md")
    if env.get("NOTION_TOKEN"):
        st["publish"] = "done"
        out["notes"].append("publish inferred done: NOTION_TOKEN is set so `report` publishes through the API "
                            "(unless no parent page is configured; check the [notion] line it printed)")
        return finish("done", "done", f"Finished. Report: {arg}/report.md")
    return finish("publish", "agent", f"Publish {arg}/report.md through the Notion connector, then record it.",
                  f"podcast-alpha mark publish end --run {arg}")


def _weekly(run, arg, manifest, out, finish, env):
    st = out["stages"]
    rank = run / "rank"
    ins = sorted(rank.glob("batch-*-in.json")) if rank.is_dir() else []
    if not ins:
        return finish("rank_prep", "command", "Condense transcripts and split them into reader batches.",
                      f"podcast-alpha rank-prep {arg}")
    st["rank_prep"] = "done"
    outs = {p.name.replace("-out.json", "") for p in rank.glob("batch-*-out.json")}
    todo = [p for p in ins if p.name.replace("-in.json", "") not in outs]
    ranked_p = run / "ranked.json"
    out["readers"] = {"batches": len(ins), "done": len(ins) - len(todo)}
    if todo and not ranked_p.exists():
        names = ", ".join(p.name for p in todo)
        return finish("readers", "agent", f"{len(todo)} of {len(ins)} batches have no output. Give each to a reader "
                      f"(one per batch): {names}; save as rank/batch-N-out.json.", prompt="prompts/rank.md")
    st["readers"] = "done"
    if not ranked_p.exists():
        return finish("rank_merge", "command", "Merge the reader outputs into ranked.json.", f"podcast-alpha rank-merge {arg}")
    st["rank_merge"] = "done"
    from . import ranked, report

    try:
        data = ranked.load(str(ranked_p))
        ranked.validate(data)
        errors, _ = report.ranked_verify(data, run)
    except (ranked.RankedError, OSError, ValueError, KeyError) as exc:
        errors = [str(exc)]
    if errors:
        out["blockers"] += errors[:10]
        return finish("rank_merge", "command", "ranked.json fails validation: fix reader outputs and rerun rank-merge.",
                      f"podcast-alpha rank-merge {arg}")
    digests = sorted(run.glob("digest*.md"), key=_mtime)
    if not digests or _mtime(digests[-1]) < _mtime(ranked_p):
        return finish("publish", "command", "Render the digest (publishes to Notion when NOTION_TOKEN is set).",
                      f"podcast-alpha publish {arg}/ranked.json --top 15")
    st["publish"] = "done"
    out["notes"].append("digest rendered; Notion publication is not recorded anywhere (publish prints the [notion] line)")
    return finish("done", "done", f"Finished. Digest: {arg}/{digests[-1].name}")


def record_boundaries(run: str | Path, info: dict | None = None) -> list[str]:
    """Stamp stage boundaries code can observe, once each, from file times. Returns what it recorded.

    select.start = first look after transcribe ended; select.end = selected.json passed check (file mtime);
    report.start = select end; report.end = report.md rendered (file mtime). Never overwrites a mark.
    """
    from .timing import mark

    run = Path(run)
    info = info or inspect_run(run)
    if info.get("kind") != "daily" or not (run / "manifest.json").exists():
        return []
    t = (_json(run / "manifest.json") or {}).get("timing") or {}
    iso = lambda p: datetime.fromtimestamp(_mtime(p), timezone.utc).isoformat(timespec="seconds")
    done, s = [], info["stages"]
    if s.get("transcribe") == "done" and not t.get("select", {}).get("start"):
        end_t = _mtime(run / "selected.json") if (run / "selected.json").exists() else None
        if end_t is None or end_t >= time.time():
            mark(run, "select", "start")
        else:  # selected.json already exists: start at its time so select.seconds is never negative
            mark(run, "select", "start", iso(run / "selected.json"))
        done.append("select start")
    if s.get("select") == "done" and not t.get("select", {}).get("end"):
        mark(run, "select", "end", iso(run / "selected.json"))
        done.append("select end")
        if not t.get("report", {}).get("start"):
            mark(run, "report", "start", iso(run / "selected.json"))
            done.append("report start")
    if s.get("render") == "done" and not t.get("report", {}).get("end"):
        mark(run, "report", "end", iso(run / "report.md"))
        done.append("report end")
    return done


def render_text(info: dict) -> str:
    lines = [f"[status] {info['run']} ({info['kind']}) stage: {info.get('stage', '?')}"]
    lines.append("  " + "  ".join(f"{k}={v}" for k, v in info["stages"].items()))
    for n in info["notes"]:
        lines.append(f"  note: {n}")
    for b in info["blockers"]:
        lines.append(f"  blocker: {b}")
    nx = info.get("next", {})
    lines.append(f"  next ({nx.get('type')}): {nx.get('summary')}")
    if nx.get("prompt"):
        lines.append(f"    follow: {nx['prompt']}")
    if nx.get("command"):
        lines.append(f"    run: {nx['command']}")
    return "\n".join(lines)
