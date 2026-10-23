from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .people import Person, mentions, parse_itunes
from .transcribe import title_match

ITEM = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+)$")
NAME = re.compile(r"^([A-Z][\w'’.-]*(?:\s[A-Z][\w'’.-]*){1,3})(?=$|,|\s+of\s)(.*)$")
START = re.compile(r"essential episodes", re.I)
STOP = re.compile(r"^(#+|THE SIGNAL|Produced by)", re.I)


def split_people(tail: str) -> list[dict]:
    out = []
    for part in re.split(r",\s*(?=[A-Z][\w'’.-]+\s[A-Z])|\s+and\s+(?=[A-Z])", tail):
        part = re.sub(r"^[A-Z][\w.-]*(?:\s[A-Z][\w.-]*)*['’]s\s+(?=[A-Z])", "", part.strip())
        m = NAME.match(part)
        if m:
            out.append({"name": m.group(1), "role": m.group(2).strip(" ,")})
        elif out and part and not out[-1]["role"]:
            out[-1]["role"] = part
    return out


def parse_email(text: str) -> list[dict]:
    lines, seeds, on = text.splitlines(), [], False
    for line in lines:
        if START.search(line):
            on = True
            continue
        if not on:
            continue
        if STOP.match(line.strip()) and seeds:
            break
        m = ITEM.match(line)
        if not m:
            continue
        head, _, tail = m.group(2).rpartition(", with ")
        head = head or m.group(2)
        people = split_people(tail)
        guests = [p for p in people if p["name"].split()[-1] in head] or people[-1:]
        seeds.append({"n": int(m.group(1)), "headline": head.strip(), "people": people, "guests": guests})
    return seeds


def covered(seed: dict, manifest: dict) -> list[str]:
    hits = []
    for e in manifest.get("episodes", []):
        text = f"{e.get('title', '')} {e.get('source', '')}"
        if title_match(seed["headline"], e.get("title", "")) >= 0.5 or any(
                mentions(Person(g["name"], "", "watch"), text) for g in seed["guests"]):
            hits.append(e["episode_id"])
    return hits


def search(seed: dict, now: datetime, days: float, fetch_json) -> list[dict]:
    out = []
    for g in seed["guests"]:
        rows = fetch_json(g["name"])
        for ep in parse_itunes(Person(g["name"], g.get("role", ""), "watch"), rows, now, days, min_minutes=10):
            out.append({"title": ep.title, "show": ep.source_name, "published": ep.published_at.date().isoformat(),
                        "url": ep.url, "audio_url": ep.audio_url, "match": round(title_match(seed["headline"], ep.title), 2)})
    return sorted(out, key=lambda r: -r["match"])[:3]


def itunes_json(term: str) -> list[dict]:
    import requests

    r = requests.get("https://itunes.apple.com/search", timeout=30,
                     params={"term": term, "media": "podcast", "entity": "podcastEpisode", "limit": 50})
    r.raise_for_status()
    return r.json().get("results", [])


def recall(seeds: list[dict], run_dir: Path) -> dict:
    """Our 3 picks (selected.json) vs TMTB's essential list."""
    sel_p = run_dir / "selected.json"
    picks = json.loads(sel_p.read_text())["episodes"] if sel_p.exists() else []
    pick_m = {"episodes": [{"episode_id": p["episode_id"], "title": p.get("title", ""),
                            "source": f"{p.get('show', '')} " + " ".join(g.get("name", "") for g in p.get("guests", []))}
                           for p in picks]}
    hits = [s for s in seeds if covered(s, pick_m)]
    n = len(seeds)
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tmtb": n, "picks": len(picks),
            "hits": len(hits), "recall": round(len(hits) / n, 3) if n else 0.0,
            "hit": [s["headline"] for s in hits], "missed": [s["headline"] for s in seeds if s not in hits],
            "found_in_run": sum(1 for s in seeds if s.get("covered_by"))}


def run(text: str, run_dir: Path, days: float = 4, fetch_json=itunes_json, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"episodes": []}
    seeds = parse_email(text)
    for s in seeds:
        s["covered_by"] = covered(s, manifest)
        s["candidates"] = [] if s["covered_by"] else search(s, now, days, fetch_json)
    missed = [s for s in seeds if not s["covered_by"]]
    (run_dir / "seeds.json").write_text(json.dumps(seeds, indent=2, ensure_ascii=False))
    (run_dir / "missed.json").write_text(json.dumps(missed, indent=2, ensure_ascii=False))
    md = [f"# Missed vs reference: {len(missed)} of {len(seeds)}", ""]
    for s in missed:
        md.append(f"- **{s['headline']}** (guests: {', '.join(g['name'] for g in s['guests']) or 'unknown'})")
        md += [f"  - candidate: [{c['title']}]({c['url']}) {c['show']}, {c['published']}" for c in s["candidates"]]
        if not s["candidates"]:
            md.append("  - no free audio found via iTunes search")
    (run_dir / "missed.md").write_text("\n".join(md) + "\n")
    rec = recall(seeds, run_dir)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["recall"] = rec
        tmp = manifest_path.with_suffix(".recall.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        tmp.replace(manifest_path)
    if rec["tmtb"]:
        print(f"[tmtb] recall: our picks hit {rec['hits']}/{rec['tmtb']} ({rec['recall']:.0%}); run found {rec['found_in_run']}")
    print(f"[tmtb] {len(seeds)} seeds, {len(seeds) - len(missed)} covered, {len(missed)} missed -> {run_dir / 'missed.md'}")
    return {"seeds": seeds, "missed": missed}
