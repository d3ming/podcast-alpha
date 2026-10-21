from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .config import load_sources
from .ingest import Episode, discover
from .people import dedupe, discover_people, load_people
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
            "transcript_minutes": round(segs[-1]["start"] / 60) if segs else 0}


def load_manifest(run_dir: Path, now: datetime, days) -> dict:
    p = run_dir / "manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"run_at": now.isoformat(timespec="seconds"), "days": days, "episodes": [], "skipped_seen": []}


def youtube_only(args, now: datetime) -> Path:
    sources, t3_cap = load_sources(args.sources)
    sources = [s for s in sources if s.youtube and (not args.only or s.id in args.only.split(","))]
    eps = discover(sources, now, args.days, t3_cap, per_source=args.per_source)
    meta = {"run_at": now.isoformat(timespec="seconds"), "days": args.days}
    if args.people:
        person_eps, meta["people"] = discover_people(load_people(args.people), now, args.days, eps, args.per_person, ("youtube",))
        eps += person_eps
    out = Path(args.out)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    rows = []
    for ep in eps:
        segs, tsrc = get_transcript(ep)
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
        manifest["episodes"].append(write_episode(run_dir, ep, segs, tsrc))
        store.add_episode(ep, tsrc)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[run] {run_dir} ({len(manifest['episodes'])} episodes)")
    return run_dir
