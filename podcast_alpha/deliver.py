"""Mac -> box delivery over a `runs` git branch, plus the box's fallback check.

The Mac runs fetch + transcribe at 4 AM and pushes the run folder (manifest, transcripts, heartbeat; never audio,
locks, databases or holdings) to the `runs` branch of origin. main stays code-only. The box's ~4:45 routine calls
`fallback-check`: a fresh or finished Mac heartbeat means pull it; a missing, failed or stale one means run daily here.
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "runs"
WORKTREE = ROOT / ".runs-worktree"
SKIP_SUFFIXES = {".mp3", ".m4a", ".mp4", ".wav", ".webm", ".opus", ".ogg", ".aac", ".part", ".lock", ".sqlite", ".db"}
MAX_BYTES = 20 * 1024 * 1024
AUTHOR = {"GIT_AUTHOR_NAME": "d3ming", "GIT_AUTHOR_EMAIL": "5472365+d3ming@users.noreply.github.com",
          "GIT_COMMITTER_NAME": "d3ming", "GIT_COMMITTER_EMAIL": "5472365+d3ming@users.noreply.github.com"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def today_name() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def git(*args, cwd: Path | None = None, check: bool = True, env: dict | None = None, binary: bool = False):
    r = subprocess.run(["git", *args], cwd=cwd or ROOT,  # ROOT read at call time so tests can repoint it
                       capture_output=True, text=not binary,
                       env={**os.environ, **(env or {})})
    if check and r.returncode != 0:
        raise SystemExit(f"[git] {' '.join(args)} failed: {r.stderr if not binary else r.stderr.decode(errors='replace')}")
    return r


def deliverable(path: Path) -> bool:
    """Run files that are safe and small enough to put on a public branch."""
    name = path.name.lower()
    if path.suffix.lower() in SKIP_SUFFIXES or "holding" in name or name.startswith(".env"):
        return False
    return path.is_file() and path.stat().st_size <= MAX_BYTES


def write_heartbeat(run_dir: Path, state: str, note: str = "") -> dict:
    from . import whisper

    run_dir.mkdir(parents=True, exist_ok=True)
    hb = {"state": state, "host": socket.gethostname(), "platform": f"{platform.system()}/{platform.machine()}",
          "backend": whisper.backend(), "at": now().isoformat(timespec="seconds"), "note": note}
    (run_dir / "heartbeat.json").write_text(json.dumps(hb, indent=2) + "\n")
    return hb


def heartbeat_cmd(args) -> None:
    print(json.dumps(write_heartbeat(Path(args.runs) / args.run, args.state, args.note)))


def remote_has_branch() -> bool:
    return git("ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False).returncode == 0


def ensure_worktree() -> Path:
    """A private worktree on `runs`, reset to origin/runs (or a new orphan branch). Never touches the main checkout."""
    if remote_has_branch():
        git("fetch", "-q", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
    if not (WORKTREE / ".git").exists():
        shutil.rmtree(WORKTREE, ignore_errors=True)
        git("worktree", "prune")
        git("worktree", "add", "-q", "--detach", str(WORKTREE))
        if remote_has_branch():
            git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}", cwd=WORKTREE)
        else:
            git("checkout", "-q", "--orphan", BRANCH, cwd=WORKTREE)
            git("rm", "-rqf", "--ignore-unmatch", ".", cwd=WORKTREE)
            for p in WORKTREE.iterdir():
                if p.name != ".git":
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
            (WORKTREE / "README.md").write_text("Daily run artifacts (manifest, transcripts, heartbeat) pushed by the "
                                                "Mac. Code lives on main. No audio, no holdings.\n")
    elif remote_has_branch():
        git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}", cwd=WORKTREE)
        git("reset", "-q", "--hard", f"origin/{BRANCH}", cwd=WORKTREE)
    return WORKTREE


def deliver(run_dir: Path, push: bool = True) -> int:
    if not run_dir.is_dir():
        raise SystemExit(f"[deliver] no run folder {run_dir}")
    wt = ensure_worktree()
    dest = wt / "runs" / run_dir.name
    shutil.rmtree(dest, ignore_errors=True)
    n = 0
    for src in sorted(run_dir.rglob("*")):
        if deliverable(src):
            out = dest / src.relative_to(run_dir)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            n += 1
    git("add", "-A", ".", cwd=wt)
    if git("diff", "--cached", "--quiet", cwd=wt, check=False).returncode != 0:
        git("commit", "-q", "-m", f"Deliver run {run_dir.name}", cwd=wt, env=AUTHOR)
    if push:
        git("push", "-q", "origin", f"{BRANCH}:{BRANCH}", cwd=wt)
    print(f"[deliver] {n} files of {run_dir.name} -> {BRANCH}" + ("" if push else " (not pushed)"))
    return n


def deliver_cmd(args) -> None:
    deliver(Path(args.runs) / args.run, push=not args.no_push)


def remote_heartbeat(name: str) -> dict | None:
    if not remote_has_branch():
        return None
    git("fetch", "-q", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
    r = git("show", f"origin/{BRANCH}:runs/{name}/heartbeat.json", check=False)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except json.JSONDecodeError:
        return None


def pull_run(name: str, runs: Path) -> int:
    """Extract origin/runs:runs/<name> into runs/<name>, overwriting delivered files, keeping local extras."""
    git("fetch", "-q", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
    tar = git("archive", "--format=tar", f"origin/{BRANCH}", f"runs/{name}", binary=True, check=False)
    if tar.returncode != 0:
        raise SystemExit(f"[pull-run] origin/{BRANCH} has no runs/{name}")
    n = 0
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            rel = Path(m.name).relative_to("runs")
            if ".." in rel.parts:
                continue
            out = runs / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(m).read())
            n += 1
    print(f"[pull-run] {n} files -> {runs / name}")
    return n


def pull_run_cmd(args) -> None:
    pull_run(args.run, Path(args.runs))


def decide(hb: dict | None, stale_minutes: float, at: datetime | None = None) -> str:
    """'pull' when the Mac finished or is still alive, 'fallback' when it never started, failed or went quiet."""
    if not hb:
        return "fallback"
    state = hb.get("state")
    if state == "done":
        return "pull"
    if state == "running":
        try:
            age = ((at or now()) - datetime.fromisoformat(hb["at"])).total_seconds() / 60
        except (KeyError, ValueError, TypeError):
            return "fallback"
        return "pull" if age <= stale_minutes else "fallback"
    return "fallback"


def fallback_check_cmd(args) -> None:
    name = args.run or today_name()
    runs = Path(args.runs)
    hb = remote_heartbeat(name)
    choice = decide(hb, args.stale_minutes)
    print(json.dumps({"run": name, "mac_heartbeat": hb, "decision": choice, "dry_run": args.dry_run}))
    if args.dry_run:
        return
    if choice == "pull":
        pull_run(name, runs)
        if hb.get("state") != "done":
            print(f"[fallback-check] Mac still transcribing; run `podcast-alpha pull-run --run {name}` again before select")
        return
    if (runs / name / "manifest.json").exists():
        print(f"[fallback-check] {runs / name} already exists on this box; not re-running daily")
        return
    print(f"[fallback-check] FALLBACK: Mac run {name} missing/failed/stale; running daily on this box")
    from .cli import main

    main(["daily", "--hours", str(args.hours), "--run-name", name, "--runs", str(runs)])
