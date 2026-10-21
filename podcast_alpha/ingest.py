import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser

from .config import Source


@dataclass
class Episode:
    id: str
    source_id: str
    source_name: str
    tier: int
    title: str
    url: str
    published_at: datetime
    youtube_id: str | None = None
    transcript_urls: list | None = None
    audio_url: str | None = None
    description: str = ""


def canonical_id(guid: str | None, youtube_id: str | None, link: str | None) -> str:
    if guid:
        return "rss:" + hashlib.sha1(guid.encode()).hexdigest()[:16]
    if youtube_id:
        return "yt:" + youtube_id
    return "url:" + hashlib.sha1((link or "").encode()).hexdigest()[:16]


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_window(dt: datetime, now: datetime, days: int) -> bool:
    return now - timedelta(days=days) <= dt <= now + timedelta(hours=1)


def transcript_links(entry) -> list[dict]:
    links = []
    for key in ("podcast_transcript",):
        val = entry.get(key)
        if isinstance(val, dict):
            links.append(val)
        elif isinstance(val, list):
            links.extend(v for v in val if isinstance(v, dict))
    for link in entry.get("links", []):
        if link.get("rel") == "transcript" or "transcript" in (link.get("type") or ""):
            links.append({"url": link.get("href"), "type": link.get("type")})
    return [l for l in links if l.get("url")]


def from_rss(src: Source, now: datetime, days: int, parsed=None) -> list[Episode]:
    feed = parsed if parsed is not None else feedparser.parse(src.rss)
    out = []
    for e in feed.entries:
        dt = parse_date(e.get("published") or e.get("updated"))
        if not dt or not in_window(dt, now, days):
            continue
        audio = next((l.get("href") for l in e.get("links", []) if (l.get("type") or "").startswith("audio")), None)
        out.append(Episode(
            id=canonical_id(e.get("id") or e.get("guid"), None, e.get("link")),
            source_id=src.id, source_name=src.name, tier=src.tier,
            title=e.get("title", "").strip(), url=e.get("link") or audio or "",
            published_at=dt, transcript_urls=transcript_links(e), audio_url=audio,
            description=e.get("summary", "")[:2000],
        ))
    return out


def _yt_json(args: list[str]) -> list[dict]:
    res = subprocess.run(["yt-dlp", "-j", "--skip-download", "--no-warnings", *args],
                         capture_output=True, text=True, timeout=300)
    return [json.loads(l) for l in res.stdout.splitlines() if l.strip()]


def from_youtube(src: Source, now: datetime, days: int, max_items: int = 8) -> list[Episode]:
    out = []
    for v in _yt_json(["--flat-playlist", "-I", f"1:{max_items}", src.youtube]):
        info = _yt_json([f"https://www.youtube.com/watch?v={v['id']}"])
        if not info:
            continue
        info = info[0]
        ts = info.get("release_timestamp") or info.get("timestamp")
        if ts:
            dt = datetime.fromtimestamp(ts, timezone.utc)
        elif info.get("upload_date"):
            dt = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            continue
        if not in_window(dt, now, days):
            if dt < now - timedelta(days=days):
                break
            continue
        if (info.get("duration") or 0) < 300:
            continue
        out.append(Episode(
            id=canonical_id(None, v["id"], None), source_id=src.id, source_name=src.name, tier=src.tier,
            title=info.get("title", ""), url=f"https://www.youtube.com/watch?v={v['id']}",
            published_at=dt, youtube_id=v["id"], description=(info.get("description") or "")[:2000],
        ))
    return out


def discover(sources: list[Source], now: datetime, days: int, t3_cap: int, per_source: int = 3) -> list[Episode]:
    eps: list[Episode] = []
    t3 = 0
    for src in sources:
        try:
            found = from_rss(src, now, days) if src.rss else from_youtube(src, now, days)
        except Exception as exc:
            print(f"[ingest] {src.id}: {type(exc).__name__}")
            continue
        found.sort(key=lambda e: e.published_at, reverse=True)
        found = found[:per_source]
        if src.tier == 3:
            found = found[: max(0, t3_cap - t3)]
            t3 += len(found)
        print(f"[ingest] {src.id}: {len(found)} in window")
        eps.extend(found)
    return eps
