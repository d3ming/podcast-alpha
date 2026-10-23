"""Per-podcast links (site, YouTube, Apple, Spotify) kept in show_links.yaml, keyed by the id in sources.yaml.

`fill` only writes blank fields, so a value you edit by hand is never overwritten. Everything it finds comes from
free, public sources: the iTunes lookup, the feed's own channel link, and links on the show's own site.
"""
from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import yaml

from .links import _get, bad_url, similar

FIELDS = ("site", "youtube", "apple", "spotify")
HEADER = ("# Per-podcast links, keyed by the id in sources.yaml. Edit any value to correct it: `podcast-alpha show-links`\n"
          "# only fills blank fields and never overwrites one that is set. Empty string = not found yet.\n")
YT_CHANNEL = re.compile(r"https?://(?:www\.)?youtube\.com/(?:@[\w.-]+|channel/UC[\w-]{22}|c/[\w.-]+)")
SPOTIFY_SHOW = re.compile(r"https?://open\.spotify\.com/show/[A-Za-z0-9]{22}")
SKIP_PREFIX = ("calls-",)
# Hosting platforms: their pages link to the platform's own YouTube channel, not the show's, so never scrape them for YouTube.
HOSTS = ("spotify.com", "buzzsprout.com", "acast.com", "rss.com", "art19.com", "simplecast.com", "libsyn.com", "anchor.fm",
         "megaphone.fm", "transistor.fm", "omny.fm", "podbean.com", "flightcast.com", "substack.com")


def _page(url: str, timeout: float = 15) -> str:
    import urllib.request
    # a bare "Mozilla/5.0" gets Spotify's server-rendered page; a browser-like UA gets the JS shell with no show title
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save(path: Path, data: dict) -> None:
    path.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=200))


def by_name(path: Path) -> dict:
    """Lower-cased show name -> links, for rendering from an episode's `source`."""
    return {v.get("name", "").lower(): v for v in load(path).values() if v.get("name")}


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(0) if m else ""


def apple_show(src: dict, get=_get) -> str:
    """Apple Podcasts show page: by itunes_id if the source has one, else a search that must match the feed URL."""
    feed = (src.get("rss") or "").rstrip("/")
    if src.get("itunes_id"):
        hits = get(f"https://itunes.apple.com/lookup?id={src['itunes_id']}").get("results", [])
    else:
        import urllib.parse
        q = urllib.parse.urlencode({"term": src["name"], "media": "podcast", "entity": "podcast", "limit": 10})
        hits = get(f"https://itunes.apple.com/search?{q}").get("results", [])
        hits = [h for h in hits if h.get("feedUrl", "").rstrip("/") == feed or similar(src["name"], h.get("collectionName", "")) >= 0.9]
        hits.sort(key=lambda h: h.get("feedUrl", "").rstrip("/") != feed)
    url = (hits[0].get("collectionViewUrl") or "") if hits else ""
    return url.split("?")[0]


def feed_site(src: dict, parse=None) -> str:
    """The channel-level <link> of the feed, if it is a real page (not a tracking or hosting URL)."""
    if parse is None:
        import feedparser
        parse = feedparser.parse
    link = (parse(src["rss"]).feed.get("link") or "").strip()
    return "" if bad_url(link) else link


def is_host(url: str) -> bool:
    return any(h in url.lower() for h in HOSTS)


def youtube_ok(url: str, name: str, fetch=_page) -> bool:
    """A channel scraped from a site must be titled like the show (youtube.com/@x -> <title>)."""
    m = re.search(r"<title>([^<]*)", fetch(url))
    title = re.sub(r"\s*-\s*YouTube\s*$", "", unescape(m.group(1))) if m else ""
    return similar(name, title) >= 0.5


def spotify_ok(url: str, name: str, fetch=_page) -> bool:
    """The show page's <title> is "<show> | Listen to the Podcast on Spotify" (oEmbed gives the latest episode, not the show)."""
    m = re.search(r"<title>([^<|]*)", fetch(url))
    return similar(name, unescape(m.group(1)).strip()) >= 0.5 if m else False


def yt_channel_search(name: str, run=None) -> str:
    """Opt-in, Mac/residential IP: yt-dlp's channel-filtered YouTube search; only a channel titled like the show is accepted."""
    import subprocess
    import sys
    import urllib.parse

    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True).stdout)
    q = urllib.parse.quote_plus(f"{name} podcast")
    out = run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", "5", "--print", "%(channel_url)s\t%(channel)s",
               f"https://www.youtube.com/results?search_query={q}&sp=EgIQAg%253D%253D"])
    for line in out.splitlines():
        url, _, chan = line.partition("\t")
        if url.startswith("https://www.youtube.com/") and similar(name, chan) >= 0.6:
            return url
    return ""


def discover(src: dict, get=_get, fetch=_page, parse=None, ytsearch: bool = False) -> dict:
    found = {f: "" for f in FIELDS}
    if src.get("youtube_channel_id"):
        found["youtube"] = f"https://www.youtube.com/channel/{src['youtube_channel_id']}"
    elif src.get("youtube"):
        found["youtube"] = re.sub(r"/(videos|streams)/?$", "", src["youtube"])
    for key, fn in (("apple", lambda: apple_show(src, get)), ("site", lambda: feed_site(src, parse))):
        try:
            found[key] = fn()
        except Exception:
            pass
    if "youtube.com/" in found["site"]:  # some feeds list the channel as the show's link
        found["youtube"] = found["youtube"] or found["site"]
        found["site"] = ""
    if found["site"]:
        try:
            html = fetch(found["site"])
        except Exception:
            html = ""
        for key, pat, ok in (("youtube", YT_CHANNEL, youtube_ok), ("spotify", SPOTIFY_SHOW, spotify_ok)):
            if found[key] or (key == "youtube" and is_host(found["site"])) or not (url := _first(pat, html)):
                continue
            try:
                found[key] = url if ok(url, src["name"], fetch) else ""
            except Exception:
                pass
    if ytsearch and not found["youtube"]:
        try:
            found["youtube"] = yt_channel_search(src["name"])
        except Exception:
            pass
    return found


def fill(sources: Path, links_path: Path, only: set[str] | None = None, discover_fn=discover, ytsearch: bool = False) -> dict:
    """Add a row per source and fill blank fields. Returns {id: [fields newly filled]}."""
    srcs = (yaml.safe_load(sources.read_text()) or {}).get("sources", [])
    data, changed = load(links_path), {}
    for src in srcs:
        sid = src.get("id")
        if not sid or sid.startswith(SKIP_PREFIX) or (only and sid not in only):
            continue
        row = data.setdefault(sid, {})
        row.setdefault("name", src["name"])
        for f in FIELDS:
            row.setdefault(f, "")
        todo = [f for f in FIELDS if not row[f]]
        if not todo:
            continue
        found = discover_fn(src, ytsearch=True) if ytsearch else discover_fn(src)
        got = [f for f in todo if found.get(f)]
        for f in got:
            row[f] = found[f]
        changed[sid] = got
    save(links_path, data)
    return changed
