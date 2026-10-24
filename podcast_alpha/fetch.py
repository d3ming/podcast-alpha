from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import load_sources
from .ingest import Episode, discover
from .people import apply_source_tiers, dedupe, discover_people, load_people
from .ingest import in_window, is_rerun
from .manifest import update as update_manifest
from .transcribe import MIN_TRANSCRIPT_CHARS, fmt_ts, get_transcript, is_fallback, title_match, transcript_chars
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


def write_episode(run_dir: Path, ep: Episode, segs, tsrc, min_chars: int = MIN_TRANSCRIPT_CHARS) -> dict:
    fallback = is_fallback(segs, min_chars)
    name = f"{ep.published_at.date()}-{slug(ep.source_id, 40)}-{slug(ep.title)}.md"
    (run_dir / name).write_text(episode_md(ep, segs, tsrc))
    print(f"[episode] {ep.source_id}: transcript={tsrc or 'none'}")
    return {"episode_id": ep.id, "file": name, "title": ep.title, "source": ep.source_name, "source_tag": ep.source_id,
            "via": ep.via or "channel", "tier": ep.tier, "published": ep.published_at.date().isoformat(), "url": ep.url,
            "transcript_source": "fallback" if fallback else tsrc, "has_transcript": bool(segs) and not fallback,
            "fallback_text": fallback, "fallback_from": tsrc if fallback else None, "transcript_chars": transcript_chars(segs),
            "transcript_minutes": round(segs[-1]["start"] / 60) if segs and not fallback else 0, "audio_url": ep.audio_url,
            "youtube_id": ep.youtube_id, "transcript_url": ep.transcript_url, "rerun": is_rerun(ep.title, ep.description), "duration": ep.duration,
            "description": re.sub(r"<[^>]+>", " ", ep.description or "")[:1500]}


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
        eps += apply_source_tiers(person_eps, load_sources(args.sources)[0])
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
    rows, skipped = [], []
    for ep, segs, tsrc in loaded:
        is_person = ep.source_id.startswith("person:")
        if (is_person and id(ep) not in keep) or ep.id in have or store.seen(ep.id):
            skipped.append(ep.id)
            print(f"[dedupe] skip {ep.id}")
            continue
        ep.via = ep.via or ("youtube" if is_person else "channel")
        rows.append(write_episode(run_dir, ep, segs, tsrc))
        store.add_episode(ep, tsrc)
        have.add(ep.id)
    entry = {"from": str(args.dir), "at": now.isoformat(timespec="seconds"), "added": len(rows)}

    def apply(m):
        ids = {e["episode_id"] for e in m.setdefault("episodes", [])}
        m["episodes"] += [r for r in rows if r["episode_id"] not in ids]
        m.setdefault("skipped_seen", []).extend(skipped)
        m.setdefault("merged", []).append(entry)

    update_manifest(run_dir / "manifest.json", apply, lambda: load_manifest(run_dir, now, src.get("days")))
    print(f"[merge] {run_dir} (+{len(rows)} episodes)")
    return run_dir


FAILED = "lookup-failed"


def safe_transcript(ep, budget):
    """One episode's lookup must never abort the whole fetch (timeouts, yt-dlp missing, blocked IP): record none and go on."""
    try:
        return get_transcript(ep, budget)
    except Exception as exc:
        print(f"[transcript] {ep.id}: {type(exc).__name__}: {str(exc)[:80]}")
        return None, FAILED


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
    lock = run_dir / "transcribe.lock"
    if lock.exists() and pid_alive(pid := lock.read_text().strip()):
        raise SystemExit(f"[fetch] {lock} held by running transcribe pid {pid}; wait for it to finish, then rerun")
    path = run_dir / "manifest.json"
    prior = json.loads(path.read_text()) if path.exists() else None
    have = {e["episode_id"] for e in (prior or {}).get("episodes", [])}
    if prior:
        print(f"[run] {run_dir} exists ({len(have)} episodes); adding only new ones")
    manifest = {"run_at": now.isoformat(timespec="seconds"), "days": args.days, "episodes": [], "skipped_seen": []}
    from .timing import apply as timing_apply

    timing_apply(manifest, "fetch", "start", now.isoformat(timespec="seconds"))
    known = store.known_since(now - timedelta(days=args.days + 7))
    people_first = getattr(args, "people_first", False)
    health = {}
    person_eps, eps = [], [] if people_first else discover(sources, now, args.days, t3_cap, per_source=args.per_source,
                                                           health=health)
    if args.people:
        person_eps, manifest["people"] = discover_people(load_people(args.people), now, args.days, eps + known, args.per_person)
        apply_source_tiers(person_eps, load_sources(args.sources)[0])
    if people_first:
        eps = [e for e in discover(sources, now, args.days, t3_cap, per_source=args.per_source, health=health)
               if not any(p.id == e.id or title_match(p.title, e.title) >= 0.8 for p in person_eps)]
    eps += person_eps
    manifest["sources"] = health
    manifest["excluded"] = []
    keep_reruns = getattr(args, "include_reruns", False)
    for e in list(eps):
        why = "outside window" if not in_window(e.published_at, now, args.days) else (
            "rerun" if is_rerun(e.title, e.description) and not keep_reruns else None)
        if why:
            eps.remove(e)
            manifest["excluded"].append({"episode_id": e.id, "title": e.title, "published": e.published_at.isoformat(), "why": why})
            print(f"[window] skip {why}: {e.title[:60]}")
    budget = Budget(args.whisper_minutes or 0, args.whisper_per_episode)
    news_ids = {s.id for s in sources if s.news}
    eps.sort(key=lambda e: (e.source_id.startswith("person:") != people_first, e.tier, -e.published_at.timestamp()))
    todo, queued = [], set()
    for ep in eps:
        if ep.id in have:
            continue
        if getattr(args, "youtube_mirror", False) and "youtube_mirror" not in (ep.methods or []):
            ep.methods = [m for m in (ep.methods or ["podcast_transcript", "whisper"]) if m != "whisper"] + \
                ["youtube_mirror", "whisper"]
        dup = not args.refetch and (ep.id in queued or store.seen(ep.id)
                                    or any(k.id != ep.id and title_match(k.title, ep.title) >= 0.8 for k in known))
        if dup:
            manifest["skipped_seen"].append(ep.id)
            print(f"[dedupe] skip {ep.id}")
            continue
        queued.add(ep.id)
        todo.append(ep)
    # Transcript lookups are network-bound and independent, so run them side by side. Whisper is CPU-bound and shares the
    # budget, so a run that may whisper stays sequential.
    workers = 1 if args.whisper_minutes else min(4, max(1, len(todo)))
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda ep: safe_transcript(ep, budget), todo))
    for ep, (segs, tsrc) in zip(todo, results):
        failed = tsrc == FAILED  # keep the row (it can still be whispered) but leave the dedupe DB alone so a later run retries
        row = write_episode(run_dir, ep, segs, None if failed else tsrc, getattr(args, "min_transcript_chars", MIN_TRANSCRIPT_CHARS))
        row["news"] = ep.source_id in news_ids
        manifest["episodes"].append(row)
        if not failed:
            store.add_episode(ep, tsrc)
    manifest["whisper_minutes"] = round(budget.used / 60, 1)
    timing_apply(manifest, "fetch", "end")
    new = manifest

    def apply(m):
        if m is new:
            return
        ids = {e["episode_id"] for e in m.setdefault("episodes", [])}
        m["episodes"] += [r for r in new["episodes"] if r["episode_id"] not in ids]
        ids |= {r["episode_id"] for r in new["episodes"]}
        seen = set(m.setdefault("skipped_seen", []))
        m["skipped_seen"] += [i for i in dict.fromkeys(new["skipped_seen"]) if i not in seen and i not in ids]
        gone = {x["episode_id"] for x in m.setdefault("excluded", [])}
        m["excluded"] += [x for x in new["excluded"] if x["episode_id"] not in gone and x["episode_id"] not in ids]
        m["whisper_minutes"] = round(m.get("whisper_minutes", 0) + budget.used / 60, 1)
        m["sources"] = new["sources"]
        if "people" in new:
            m["people"] = new["people"]
        if not (m.get("timing", {}).get("fetch") or {}).get("end"):
            timing_apply(m, "fetch", "start", new["timing"]["fetch"]["start"])
            timing_apply(m, "fetch", "end", new["timing"]["fetch"]["end"])

    final = update_manifest(path, apply, lambda: new)
    print(f"[run] {run_dir} ({len(final['episodes'])} episodes, +{len(new['episodes'])} new)")
    return run_dir


def record_failure(run_dir: Path, manifest: dict, e: dict, exc: Exception) -> dict:
    """Append to manifest['failures'] and transcribe.failed.log (one JSON line per failure)."""
    from .timing import now_iso
    from .whisper import DownloadError

    rec = {"at": now_iso(), "episode_id": e["episode_id"], "title": e.get("title", "")[:120], "stage": "transcribe",
           "error": str(exc) if isinstance(exc, DownloadError) else f"{type(exc).__name__}: {str(exc)[:200]}",
           "attempts": getattr(exc, "attempts", 1), "errors": getattr(exc, "errors", [type(exc).__name__]),
           "transient": getattr(exc, "transient", None)}
    manifest.setdefault("failures", []).append(rec)
    with open(run_dir / "transcribe.failed.log", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"FAILED {e['episode_id']} after {rec['attempts']} attempt(s): {rec['error']}")
    return rec


def transcribe_run(args) -> Path:
    from .store import Store
    from .whisper import DownloadError, transcribe

    run_dir = Path(args.run)
    path = run_dir / "manifest.json"
    budget, store = Budget(args.minutes, args.per_episode), Store(args.db) if args.db else None
    retry, reruns = getattr(args, "retry_errors", False), getattr(args, "include_reruns", False)
    min_chars, tried = getattr(args, "min_transcript_chars", MIN_TRANSCRIPT_CHARS), set()
    from .whisper import available

    if not available():
        raise SystemExit(f"[transcribe] faster_whisper is not importable from {sys.executable}; "
                         f"run with the venv that has it (e.g. /workspace/pa-venv/bin/podcast-alpha) or set PA_PYTHON")
    skip_news = getattr(args, "skip_news", False)
    lock = run_dir / "transcribe.lock"
    if lock.exists() and not args.force:
        pid = lock.read_text().strip()
        if pid_alive(pid):
            raise SystemExit(f"[transcribe] {lock} held by running pid {pid} (use --force)")
        log(f"stale lock from dead pid {pid or '?'}; taking over")
    lock.write_text(str(os.getpid()))
    reuse = ReuseIndex(run_dir.parent, exclude=run_dir)
    from .timing import apply as timing_apply

    top = getattr(args, "top", 0) or 0
    def start(m):
        timing_apply(refresh_flags(m, run_dir, min_chars), "transcribe", "start")
        if top and ("triage" not in m or getattr(args, "retriage", False)):
            from .triage import triage

            people = load_people(args.people) if getattr(args, "people", None) and Path(args.people).exists() else []
            picked = triage(m, people, top, is_news=is_news, run_dir=run_dir)
            log(f"triage: whisper {len(picked)} of {m['triage']['candidates']} (top {top} + core people)")

    try:
        update_manifest(path, start)
        while True:
            manifest = json.loads(path.read_text())
            todo = [e for e in manifest["episodes"] if not e["has_transcript"] and e.get("audio_url") 
                    and e["episode_id"] not in tried and (retry or not e.get("whisper_error"))
                    and not (e.get("rerun") and not reruns) and not (skip_news and is_news(e))
                    and (not top or e.get("triage", {}).get("whisper", True))]
            todo.sort(key=lambda e: (is_news(e), -e.get("triage", {}).get("score", 0),
                                     not e.get("source_tag", "").startswith("person:"), e["episode_id"]))
            if not todo or budget.take() < 60:
                break
            e = todo[0]
            before, fails = dict(e), {}
            tried.add(e["episode_id"])
            e.pop("whisper_error", None)
            log(f"start {e['episode_id']} ({len(todo)} left, {round(budget.left / 60)} min budget) {e['title'][:60]}")
            hit = reuse.find(e)
            try:
                if hit:
                    segs, tsrc, used = hit[0], hit[1], 0.0
                    log(f"reuse {e['episode_id']} from {hit[2]}")
                    if hit[3]:
                        e["transcript_url"] = hit[3]
                else:
                    t0 = time.monotonic()
                    segs, used = transcribe(e["audio_url"], budget)
                    tsrc = f"whisper ({round(used / 60)} min)"
                    log(f"whispered {round(used / 60)} audio min in {round(time.monotonic() - t0)} s wall")
            except Exception as exc:
                segs, tsrc, used = None, None, 0.0
                e["whisper_error"] = str(exc) if isinstance(exc, DownloadError) else type(exc).__name__
                record_failure(run_dir, fails, e, exc)
            if segs and is_fallback(segs, min_chars):
                segs, e["whisper_error"] = None, "short"
            if segs:
                md = run_dir / e["file"]
                md.write_text(with_transcript(md.read_text(), segs, tsrc))
                e.update(has_transcript=True, transcript_source=tsrc, transcript_minutes=round(segs[-1]["start"] / 60),
                         fallback_text=False, transcript_chars=transcript_chars(segs))
                if store:
                    store.set_transcript(e["episode_id"], tsrc)
            elif not e.get("whisper_error"):
                e["whisper_error"] = "empty"
            changed = {k: v for k, v in e.items() if k not in before or before[k] != v}
            dropped = [k for k in before if k not in e]

            def apply(m):  # only this iteration's changes, onto the fresh manifest
                for row in m["episodes"]:
                    if row["episode_id"] == e["episode_id"]:
                        row.update(changed)
                        for k in dropped:
                            row.pop(k, None)
                m["whisper_minutes"] = round(m.get("whisper_minutes", 0) + (used if segs else 0) / 60, 1)
                if fails:
                    m.setdefault("failures", []).extend(fails["failures"])

            update_manifest(path, apply)
            log(f"done {e['episode_id']} {tsrc or e.get('whisper_error')}")
    finally:
        lock.unlink(missing_ok=True)
        try:
            update_manifest(path, lambda m: timing_apply(m, "transcribe", "end"))
        except (OSError, ValueError):
            pass
    log(f"finished, {round(budget.used / 60, 1)} min used")
    return run_dir


NEWS = re.compile(r"halftime|squawk|power lunch|closing bell|fast money|mad money|news briefing|morning briefing|"
                  r"the exchange|worldwide exchange|markets daily|bloomberg surveillance", re.I)


def is_news(e: dict) -> bool:
    return bool(e.get("news")) or bool(NEWS.search(f"{e.get('source', '')} {e.get('source_tag', '')}"))


def pid_alive(pid: str) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


TRACKER = re.compile(r"^(dts\.podtrac\.com/redirect\.mp3/|www\.podtrac\.com/pts/redirect\.mp3/|chtbl\.com/track/[^/]+/|"
                     r"pdst\.fm/e/|prfx\.byspotify\.com/e/|tracking\.swap\.fm/track/[^/]+/|claritaspod\.com/measure/|"
                     r"arttrk\.com/p/[^/]+/|verifi\.podscribe\.com/rss/p/|pfx\.vpixl\.com/[^/]+/|mgln\.ai/e/[^/]+/|op3\.dev/e/)")


def norm_audio(u: str | None) -> str:
    """Host + path of the enclosure with query and tracking-redirect prefixes (podtrac, swap.fm, ...) stripped."""
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u.split("?", 1)[0].lower())
    while (m := TRACKER.match(u)):
        u = re.sub(r"^https?://", "", u[m.end():])
    return u


def segments_from_md(text: str) -> list[dict]:
    def parse_ts(t: str) -> float:
        sec = 0
        for part in t.split(":"):
            sec = sec * 60 + int(part)
        return float(sec)

    body = text.partition("## Transcript")[2]
    out = []
    for line in body.splitlines():
        m = re.match(r"^\[([\d:]+)\]\s*(.*)$", line)
        if m:
            out.append({"start": parse_ts(m.group(1)), "speaker": None, "text": m.group(2)})
    return out


class ReuseIndex:
    """Transcripts already made in other run folders, keyed by episode id, enclosure, and show + title."""

    def __init__(self, runs: Path, exclude: Path | None = None):
        self.by = {}
        for mp in sorted(runs.glob("*/manifest.json")):
            if exclude and mp.parent.resolve() == exclude.resolve():
                continue
            try:
                eps = json.loads(mp.read_text()).get("episodes", [])
            except (OSError, ValueError):
                continue
            for e in eps:
                if e.get("has_transcript"):
                    for k in self.keys(e):
                        self.by.setdefault(k, (mp.parent, e))

    @staticmethod
    def keys(e: dict) -> list[str]:
        ks = [f"id:{e['episode_id']}"]
        if norm_audio(e.get("audio_url")):
            ks.append(f"audio:{norm_audio(e.get('audio_url'))}")
        if norm_title(e.get("title")):
            ks.append(f"title:{norm_title(e.get('source') or e.get('source_tag', '').split(',')[0])}|{norm_title(e['title'])}")
        return ks

    def find(self, e: dict):
        for k in self.keys(e):
            if k in self.by:
                d, old = self.by[k]
                md = d / old["file"]
                if md.exists():
                    segs = segments_from_md(md.read_text())
                    if segs and transcript_chars(segs) >= MIN_TRANSCRIPT_CHARS:
                        return segs, old["transcript_source"], f"{d.name}/{old['file']}", old.get("transcript_url")
        return None


def refresh_flags(manifest: dict, run_dir: Path, min_chars: int) -> dict:
    for e in manifest["episodes"]:
        md = run_dir / e["file"]
        head, _, body = (md.read_text() if md.exists() else "").partition("## Transcript")
        text = re.sub(r"^\[[\d:]+\]\s*", "", body, flags=re.M).strip()
        e.setdefault("rerun", is_rerun(e["title"], head.partition("## Description")[2]))
        if e["has_transcript"] and len(text) < min_chars:
            e.update(has_transcript=False, fallback_text=True, fallback_from=e["transcript_source"],
                     transcript_source="fallback", transcript_chars=len(text), transcript_minutes=0)
            e.pop("whisper_error", None)
            log(f"fallback {e['episode_id']} ({len(text)} chars) {e['title'][:60]}")
    return manifest


def log(msg: str) -> None:
    print(f"[transcribe] {datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def with_transcript(md: str, segs: list[dict], tsrc: str) -> str:
    head, _, _ = md.partition("## Transcript")
    head = re.sub(r"^transcript_source: .*$", f"transcript_source: {tsrc}", head, count=1, flags=re.M)
    lines = [f"[{fmt_ts(s['start'])}] {s['speaker'] + ': ' if s.get('speaker') else ''}{s['text']}" for s in segs]
    return head + "## Transcript\n\n" + "\n".join(lines) + "\n"


ROOT = Path(__file__).resolve().parent.parent


def has_whisper(python: str) -> bool:
    import subprocess

    try:
        return subprocess.run([python, "-c", "import faster_whisper, podcast_alpha"], capture_output=True, timeout=120,
                              cwd=ROOT).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def whisper_python() -> str:
    """Interpreter for the background transcribe: PA_PYTHON, this one, the repo .venv, then /workspace/pa-venv."""
    cands = [os.environ.get("PA_PYTHON"), sys.executable, str(ROOT / ".venv" / "bin" / "python"), "/workspace/pa-venv/bin/python"]
    for c in dict.fromkeys(x for x in cands if x):
        if (c == sys.executable or Path(c).exists()) and has_whisper(c):
            return c
    raise SystemExit("[daily] no interpreter with faster_whisper (tried " + ", ".join(x for x in cands if x) +
                     "). Install with `pip install -e '.[whisper]'` or set PA_PYTHON; rerun with --no-transcribe to skip.")


def queue_transcribe(run_dir: Path, minutes: float, db: str | None, python: str | None = None, skip_news: bool = False,
                     top: int = 8, people: str | None = None) -> int:
    import subprocess

    cmd = [python or whisper_python(), "-m", "podcast_alpha.cli", "transcribe", str(run_dir), "--minutes", str(minutes)]
    if db:
        cmd += ["--db", db]
    if skip_news:
        cmd.append("--skip-news")
    cmd += ["--top", str(top)] + (["--people", str(people)] if people else [])
    with open(run_dir / "transcribe.log", "a") as log_file:
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
                                cwd=Path(__file__).resolve().parent.parent)
    return proc.pid


def daily(args) -> Path:
    args.days, args.people_first, args.youtube_only = args.hours / 24, True, False
    python = None if args.no_transcribe else whisper_python()  # fail fast, before the slow fetch
    run_dir = fetch(args)
    from .timing import errored_sources

    health = json.loads((run_dir / "manifest.json").read_text()).get("sources") or {}
    bad = errored_sources(health)
    print(f"[daily] source errors {len(bad)}/{len(health)}: {'; '.join(bad)}" if bad else
          f"[daily] source errors 0/{len(health)}")
    if args.tmtb:
        from .tmtb import run as tmtb_run

        tmtb_run(Path(args.tmtb).read_text(), run_dir, days=max(args.days, 2))
    if args.no_transcribe:
        print(f"[daily] transcribe later: /workspace/pa-venv/bin/podcast-alpha transcribe {run_dir}")
    else:
        pid = queue_transcribe(run_dir, args.minutes, args.db, python, getattr(args, "skip_news", False),
                               getattr(args, "top", 8), getattr(args, "people", None))
        print(f"[daily] transcribe queued pid={pid} python={python} log={run_dir / 'transcribe.log'}")
    return run_dir
