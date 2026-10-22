from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import load_sources
from .ingest import Episode, discover
from .people import dedupe, discover_people, load_people
from .transcribe import fmt_ts, get_transcript, title_match
from .whisper import Budget


def slug(text: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].rstrip("-")


def episode_md(ep, segs: list[dict] | None, tsrc: str | None) -> str:
    front = {"episode_id": ep.id, "title": ep.title, "source": ep.source_name, "source_id": ep.source_id,
             "tier": ep.tier, "published": ep.published_at.date().isoformat(), "url": ep.url,
             "youtube_id": ep.youtube_id, "transcript_source": tsrc or "none"}
    body = ["---", yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip(), "---", "", f"# {ep.title}", ""]
    if ep.description:
        body += ["## Description", "", re.sub(r"<[^>]+>", " ", ep.description).strip(), ""]
    body += ["## Transcript", ""]
    if not segs:
        body.append("_No transcript found._")
    for s in segs or []:
        who = f"{s['speaker']}: " if s.get("speaker") else ""
        body.append(f"[{fmt_ts(s['start'])}] {who}{s['text']}")
    return "\n".join(body) + "\n"


def ep_to_dict(ep: Episode, segs, tsrc) -> dict:
    return {**asdict(ep), "published_at": ep.published_at.isoformat(), "segments": segs, "transcript_source": tsrc}


def ep_from_dict(d: dict) -> tuple[Episode, list | None, str | None]:
    d = dict(d)
    segs, tsrc = d.pop("segments", None), d.pop("transcript_source", None)
    return Episode(**{**d, "published_at": datetime.fromisoformat(d["published_at"])}), segs, tsrc


def write_episode(run_dir: Path, ep: Episode, segs, tsrc) -> dict:
    name = f"{ep.published_at.date()}-{slug(ep.source_id, 40)}-{slug(ep.title)}.md"
    (run_dir / name).write_text(episode_md(ep, segs, tsrc))
    print(f"[episode] {ep.source_id}: transcript={tsrc or 'none'}")
    return {"episode_id": ep.id, "file": name, "title": ep.title, "source": ep.source_name, "source_tag": ep.source_id,
            "via": ep.via or "channel", "tier": ep.tier, "published": ep.published_at.date().isoformat(), "url": ep.url,
            "transcript_source": tsrc, "has_transcript": bool(segs),
            "transcript_minutes": round(segs[-1]["start"] / 60) if segs else 0, "audio_url": ep.audio_url,
            "youtube_id": ep.youtube_id}


def load_manifest(run_dir: Path, now: datetime, days) -> dict:
    p = run_dir / "manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"run_at": now.isoformat(timespec="seconds"), "days": days, "episodes": [], "skipped_seen": []}


def youtube_only(args, now: datetime) -> Path:
    sources, t3_cap = load_sources(args.sources)
    sources = [replace(s, preferred_source="ytdlp", fallback=[], transcripts=["youtube_captions"])
               for s in sources if s.youtube and (not args.only or s.id in args.only.split(","))]
    eps = discover(sources, now, args.days, t3_cap, per_source=args.per_source)
    meta = {"run_at": now.isoformat(timespec="seconds"), "days": args.days}
    if args.people:
        person_eps, meta["people"] = discover_people(load_people(args.people), now, args.days, eps, args.per_person, ("ytdlp",))
        eps += person_eps
    out = Path(args.out)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    rows, budget = [], Budget(600 if args.whisper_minutes is None else args.whisper_minutes, args.whisper_per_episode)
    for ep in eps:
        ep.methods = ["youtube_captions", "youtube_audio"]
        segs, tsrc = get_transcript(ep, budget)
        rows.append(ep_to_dict(ep, segs, tsrc))
        (out / "transcripts" / f"{slug(ep.id)}.md").write_text(episode_md(ep, segs, tsrc))
        print(f"[episode] {ep.source_id}: transcript={tsrc or 'none'}")
    (out / "episodes.json").write_text(json.dumps({**meta, "episodes": rows}, indent=2, ensure_ascii=False))
    print(f"[out] {out} ({len(rows)} episodes, {sum(bool(r['segments']) for r in rows)} with transcripts)")
    return out


def merge(args) -> Path:
    from .store import Store

    src = json.loads((Path(args.dir) / "episodes.json").read_text())
    now = datetime.now(timezone.utc)
    store, run_dir = Store(args.db), Path(args.into)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(run_dir, now, src.get("days"))
    have = {e["episode_id"] for e in manifest["episodes"]}
    known = [Episode(id=e["episode_id"], source_id=e.get("source_tag", ""), source_name="", tier=0, title=e["title"], url="",
                     published_at=now) for e in manifest["episodes"]]
    known += store.known_since(now - timedelta(days=(src.get("days") or 7) + 7))
    loaded = [ep_from_dict(d) for d in src["episodes"]]
    channel = [x for x in loaded if not x[0].source_id.startswith("person:")]
    people = dedupe(known + [x[0] for x in channel], [x[0] for x in loaded if x[0].source_id.startswith("person:")])
    keep = {id(e) for e in people}
    added = 0
    for ep, segs, tsrc in loaded:
        is_person = ep.source_id.startswith("person:")
        if (is_person and id(ep) not in keep) or ep.id in have or store.seen(ep.id):
            manifest["skipped_seen"].append(ep.id)
            print(f"[dedupe] skip {ep.id}")
            continue
        ep.via = ep.via or ("youtube" if is_person else "channel")
        manifest["episodes"].append(write_episode(run_dir, ep, segs, tsrc))
        store.add_episode(ep, tsrc)
        have.add(ep.id)
        added += 1
    manifest.setdefault("merged", []).append({"from": str(args.dir), "at": now.isoformat(timespec="seconds"), "added": added})
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[merge] {run_dir} (+{added} episodes)")
    return run_dir


def fetch(args) -> Path:
    now = datetime.now(timezone.utc)
    if args.youtube_only:
        return youtube_only(args, now)
    from .store import Store

    sources, t3_cap = load_sources(args.sources)
    if args.only:
        sources = [s for s in sources if s.id in args.only.split(",")]
    store = Store(args.db)
    run_dir = Path(args.runs) / (args.run_name or now.strftime("%Y-%m-%d"))
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_at": now.isoformat(timespec="seconds"), "days": args.days, "episodes": [], "skipped_seen": []}
    known = store.known_since(now - timedelta(days=args.days + 7))
    people_first = getattr(args, "people_first", False)
    person_eps, eps = [], [] if people_first else discover(sources, now, args.days, t3_cap, per_source=args.per_source)
    if args.people:
        person_eps, manifest["people"] = discover_people(load_people(args.people), now, args.days, eps + known, args.per_person)
    if people_first:
        eps = [e for e in discover(sources, now, args.days, t3_cap, per_source=args.per_source)
               if not any(p.id == e.id or title_match(p.title, e.title) >= 0.8 for p in person_eps)]
    eps += person_eps
    budget = Budget(args.whisper_minutes or 0, args.whisper_per_episode)
    eps.sort(key=lambda e: (e.source_id.startswith("person:") != people_first, e.tier, -e.published_at.timestamp()))
    for ep in eps:
        dup = not args.refetch and (store.seen(ep.id) or any(k.id != ep.id and title_match(k.title, ep.title) >= 0.8 for k in known))
        if dup:
            manifest["skipped_seen"].append(ep.id)
            print(f"[dedupe] skip {ep.id}")
            continue
        segs, tsrc = get_transcript(ep, budget)
        manifest["episodes"].append(write_episode(run_dir, ep, segs, tsrc))
        store.add_episode(ep, tsrc)
    manifest["whisper_minutes"] = round(budget.used / 60, 1)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[run] {run_dir} ({len(manifest['episodes'])} episodes)")
    return run_dir


def transcribe_run(args) -> Path:
    from .store import Store
    from .whisper import transcribe

    run_dir = Path(args.run)
    path = run_dir / "manifest.json"
    budget, store = Budget(args.minutes, args.per_episode), Store(args.db) if args.db else None
    lock = run_dir / "transcribe.lock"
    if lock.exists() and not args.force:
        raise SystemExit(f"[transcribe] {lock} exists; another job may be running (use --force)")
    lock.write_text(str(os.getpid()))
    try:
        while True:
            manifest = json.loads(path.read_text())
            todo = [e for e in manifest["episodes"] if not e["has_transcript"] and e.get("audio_url") and not e.get("whisper_error")]
            todo.sort(key=lambda e: not e.get("source_tag", "").startswith("person:"))
            if not todo or budget.take() < 60:
                break
            e = todo[0]
            log(f"start {e['episode_id']} ({len(todo)} left, {round(budget.left / 60)} min budget) {e['title'][:60]}")
            try:
                segs, used = transcribe(e["audio_url"], budget)
                tsrc = f"whisper ({round(used / 60)} min)"
            except Exception as exc:
                segs, tsrc, e["whisper_error"] = None, None, type(exc).__name__
            if segs:
                md = run_dir / e["file"]
                md.write_text(with_transcript(md.read_text(), segs, tsrc))
                e.update(has_transcript=True, transcript_source=tsrc, transcript_minutes=round(segs[-1]["start"] / 60))
                if store:
                    store.set_transcript(e["episode_id"], tsrc)
            elif not e.get("whisper_error"):
                e["whisper_error"] = "empty"
            manifest["whisper_minutes"] = round(manifest.get("whisper_minutes", 0) + (used if segs else 0) / 60, 1)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
            tmp.replace(path)
            log(f"done {e['episode_id']} {tsrc or e.get('whisper_error')}")
    finally:
        lock.unlink(missing_ok=True)
    log(f"finished, {round(budget.used / 60, 1)} min used")
    return run_dir


def log(msg: str) -> None:
    print(f"[transcribe] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def with_transcript(md: str, segs: list[dict], tsrc: str) -> str:
    head, _, _ = md.partition("## Transcript")
    head = re.sub(r"^transcript_source: .*$", f"transcript_source: {tsrc}", head, count=1, flags=re.M)
    lines = [f"[{fmt_ts(s['start'])}] {s['speaker'] + ': ' if s.get('speaker') else ''}{s['text']}" for s in segs]
    return head + "## Transcript\n\n" + "\n".join(lines) + "\n"


def queue_transcribe(run_dir: Path, minutes: float, db: str | None) -> int:
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "podcast_alpha.cli", "transcribe", str(run_dir), "--minutes", str(minutes)]
    if db:
        cmd += ["--db", db]
    with open(run_dir / "transcribe.log", "a") as log_file:
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
                                cwd=Path(__file__).resolve().parent.parent)
    return proc.pid


def daily(args) -> Path:
    args.days, args.people_first, args.youtube_only = args.hours / 24, True, False
    run_dir = fetch(args)
    if args.no_transcribe:
        print(f"[daily] transcribe later: podcast-alpha transcribe {run_dir}")
    else:
        pid = queue_transcribe(run_dir, args.minutes, args.db)
        print(f"[daily] transcribe queued pid={pid} log={run_dir / 'transcribe.log'}")
    return run_dir
