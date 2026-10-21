import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import load_sources
from .ingest import discover
from .people import discover_people, load_people
from .store import Store
from .transcribe import fmt_ts, get_transcript


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


def fetch(args) -> Path:
    now = datetime.now(timezone.utc)
    sources, t3_cap = load_sources(args.sources)
    if args.only:
        sources = [s for s in sources if s.id in args.only.split(",")]
    store = Store(args.db)
    run_dir = Path(args.runs) / (args.run_name or now.strftime("%Y-%m-%d"))
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"run_at": now.isoformat(timespec="seconds"), "days": args.days, "episodes": [], "skipped_seen": []}
    eps = discover(sources, now, args.days, t3_cap, per_source=args.per_source)
    if args.people:
        person_eps, manifest["people"] = discover_people(
            load_people(args.people), now, args.days, eps + store.known_since(now - timedelta(days=args.days + 7)), args.per_person)
        eps += person_eps
    for ep in eps:
        if store.seen(ep.id) and not args.refetch:
            manifest["skipped_seen"].append(ep.id)
            print(f"[dedupe] skip {ep.id}")
            continue
        segs, tsrc = get_transcript(ep)
        name = f"{ep.published_at.date()}-{slug(ep.source_id, 40)}-{slug(ep.title)}.md"
        (run_dir / name).write_text(episode_md(ep, segs, tsrc))
        store.add_episode(ep, tsrc)
        manifest["episodes"].append({
            "episode_id": ep.id, "file": name, "title": ep.title, "source": ep.source_name, "source_tag": ep.source_id,
            "via": ep.via or "channel", "tier": ep.tier,
            "published": ep.published_at.date().isoformat(), "url": ep.url, "transcript_source": tsrc,
            "has_transcript": bool(segs), "transcript_minutes": round(segs[-1]["start"] / 60) if segs else 0})
        print(f"[episode] {ep.source_id}: transcript={tsrc or 'none'}")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[run] {run_dir} ({len(manifest['episodes'])} episodes)")
    return run_dir
