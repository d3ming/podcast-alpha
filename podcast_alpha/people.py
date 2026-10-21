from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import yaml

from .ingest import Episode, _yt_json, canonical_id, in_window, parse_date
from .transcribe import title_match

CLIP = re.compile(r"#shorts|\bclips?\b|\breacts?\b|\breaction\b|\bhighlights?\b|\bexplained\b|\bAI voice\b", re.I)
WINDOW = [(1, "EgQIAhAB"), (7, "EgQIAxAB"), (31, "EgQIBBAB"), (366, "EgQIBRAB")]


@dataclass
class Person:
    name: str
    role: str
    priority: str
    aliases: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [self.name, *self.aliases]

    @property
    def tag(self) -> str:
        return f"person:{self.name}"


def load_people(path: str | Path) -> list[Person]:
    people = [Person(**p) for p in yaml.safe_load(Path(path).read_text())["people"]]
    for p in people:
        if p.priority not in ("core", "watch"):
            raise ValueError(f"{p.name}: priority must be core or watch")
    if len({p.name for p in people}) != len(people):
        raise ValueError("duplicate person name")
    return people


def mentions(person: Person, *texts: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    hay = f" {norm(' '.join(t or '' for t in texts))} "
    return any(f" {norm(n)} " in hay for n in person.names)


def is_clip(title: str, channel: str, description: str, duration: float | None, min_minutes: int) -> bool:
    if (duration or 0) < min_minutes * 60:
        return True
    return bool(CLIP.search(title) or CLIP.search(channel or "") or re.search(r"full (video|episode|interview):\s*http", description or "", re.I))


def _ep(person: Person, **kw) -> Episode:
    return Episode(source_id=person.tag, source_name=kw.pop("show"), tier=2, **kw)


def youtube_candidates(person: Person, min_minutes: int, rows: list[dict]) -> list[dict]:
    return [r for r in rows if mentions(person, r.get("title"), r.get("description"))
            and not is_clip(r.get("title", ""), r.get("channel", ""), r.get("description", ""), r.get("duration"), min_minutes)]


def from_youtube_search(person: Person, now: datetime, days: int, limit: int = 3, min_minutes: int = 20) -> list[Episode]:
    sp = next(code for d, code in WINDOW if days <= d)
    url = f"https://www.youtube.com/results?search_query={quote_plus(person.name + ' interview')}&sp={sp}"
    out = []
    for r in youtube_candidates(person, min_minutes, _yt_json(["--flat-playlist", "-I", "1:20", url]))[:limit * 2]:
        info = (_yt_json([f"https://www.youtube.com/watch?v={r['id']}"]) or [None])[0]
        if not info:
            continue
        ts = info.get("release_timestamp") or info.get("timestamp")
        dt = datetime.fromtimestamp(ts, timezone.utc) if ts else None
        if not dt or not in_window(dt, now, days):
            continue
        out.append(_ep(person, id=canonical_id(None, r["id"], None), show=info.get("channel") or r.get("channel", ""),
                       title=info.get("title", ""), url=f"https://www.youtube.com/watch?v={r['id']}", published_at=dt,
                       youtube_id=r["id"], description=(info.get("description") or "")[:2000]))
        if len(out) >= limit:
            break
    return out


def parse_itunes(person: Person, results: list[dict], now: datetime, days: int, min_minutes: int = 20) -> list[Episode]:
    out = []
    for r in results:
        dt = parse_date(r.get("releaseDate"))
        if not dt or not in_window(dt, now, days) or (r.get("trackTimeMillis") or 0) < min_minutes * 60000:
            continue
        if not mentions(person, r.get("trackName"), r.get("description")):
            continue
        out.append(_ep(person, id=canonical_id(r.get("episodeGuid"), None, r.get("trackViewUrl")), show=r.get("collectionName", ""),
                       title=r.get("trackName", ""), url=r.get("trackViewUrl") or r.get("episodeUrl", ""), published_at=dt,
                       audio_url=r.get("episodeUrl"), description=(r.get("description") or "")[:2000]))
    return out


def from_itunes(person: Person, now: datetime, days: int) -> list[Episode]:
    import requests

    r = requests.get("https://itunes.apple.com/search", timeout=30,
                     params={"term": person.name, "media": "podcast", "entity": "podcastEpisode", "limit": 50})
    r.raise_for_status()
    return parse_itunes(person, r.json().get("results", []), now, days)


def dedupe(channel_eps: list[Episode], person_eps: list[Episode], threshold: float = 0.8) -> list[Episode]:
    kept, ids = [], {e.id for e in channel_eps}
    titles = [e.title for e in channel_eps]
    for e in person_eps:
        dup = next((k for k in kept if k.id == e.id or title_match(k.title, e.title) >= threshold), None)
        if dup and dup.source_id != e.source_id and e.source_id not in dup.source_id.split(","):
            dup.source_id += "," + e.source_id
        if dup or e.id in ids or any(title_match(t, e.title) >= threshold for t in titles):
            continue
        kept.append(e)
        ids.add(e.id)
    return kept


def discover_people(people: list[Person], now: datetime, days: int, channel_eps: list[Episode], per_person: int = 3,
                    via: tuple[str, ...] = ("youtube", "itunes")) -> tuple[list[Episode], dict]:
    found, counts, errors = [], {v: 0 for v in via}, []
    fns = {"youtube": from_youtube_search, "itunes": from_itunes}
    for p in sorted(people, key=lambda p: p.priority != "core"):
        for src, fn in ((v, fns[v]) for v in via):
            try:
                eps = fn(p, now, days)
            except Exception as exc:
                errors.append(f"{p.name}/{src}: {type(exc).__name__}")
                continue
            found += [replace(e, via=src) for e in eps[:per_person]]
            print(f"[people] {p.name}/{src}: {len(eps)}")
    kept = dedupe(channel_eps, found)
    for e in kept:
        counts[e.via] += 1
    return kept, {"per_source": counts, "candidates": len(found), "errors": errors,
                  "skipped_sources": ["listen-notes", "podcast-index"]}
