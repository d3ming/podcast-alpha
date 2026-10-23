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


HEAD_NAME = re.compile(r"\b([A-Z][a-z][\w'’.-]*\s(?:[A-Z]\.\s)?[A-Z][a-z][\w'’.-]+)\b")
NOT_NAME = re.compile(r"^(The|Why|How|What|On|Inside|Plus|Morning|Call|Podcast|Show|Founder|CEO|CTO)\b")


def headline_guests(*texts: str) -> list[dict]:
    """Fallback: person-like name that also appears in the headline."""
    head = texts[0]
    for t in texts[1:] + (head,):
        for m in HEAD_NAME.finditer(t):
            name = m.group(1)
            if name in head and not NOT_NAME.match(name) and not any(w in name for w in ("’s", "'s")):
                return [{"name": name, "role": ""}]
    return []


def _seed(n: int, head: str, show_line: str, tail: str) -> dict:
    people = split_people(tail) if tail else []
    guests = [p for p in people if p["name"].split()[-1] in head] or people[-1:]
    for g in guests:  # 'Intel CTO Pushkar Ranade' -> 'Pushkar Ranade' when the short name is in the headline
        w = g["name"].split()
        if len(w) > 2 and " ".join(w[-2:]) in head:
            g["name"] = " ".join(w[-2:])
    if not guests:
        guests = headline_guests(head, show_line)
    return {"n": n, "headline": head.strip(), "show": show_line.strip(), "people": people, "guests": guests}


def parse_email(text: str) -> list[dict]:
    """Numbered ('1. Headline, with X') or unnumbered two-line (headline / show, with X) format."""
    lines, on, body = text.splitlines(), False, []
    for line in lines:
        if not on:
            if START.search(line):
                on = True
            continue
        t = line.strip()
        if STOP.match(t) and body:
            break
        if t:
            body.append(t)
    numbered = [ITEM.match(t) for t in body if ITEM.match(t)]
    if numbered:
        seeds = []
        for m in numbered:
            head, _, tail = m.group(2).rpartition(", with ")
            seeds.append(_seed(int(m.group(1)), head or m.group(2), "", tail))
        return seeds
    seeds = []
    for i in range(0, len(body) - 1, 2):
        head, show = body[i], body[i + 1]
        show_name, _, tail = show.partition(", with ")
        seeds.append(_seed(len(seeds) + 1, head, show_name, tail))
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
    if not seeds:
        raise SystemExit("[tmtb] ERROR: parsed 0 Essential Episodes seeds; email format changed? Not writing missed.md")
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
        from .manifest import update

        update(manifest_path, lambda m: m.update(recall=rec))
    if rec["tmtb"]:
        print(f"[tmtb] recall: our picks hit {rec['hits']}/{rec['tmtb']} ({rec['recall']:.0%}); run found {rec['found_in_run']}")
    print(f"[tmtb] {len(seeds)} seeds, {len(seeds) - len(missed)} covered, {len(missed)} missed -> {run_dir / 'missed.md'}")
    return {"seeds": seeds, "missed": missed}
