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
# any way a page or feed names a Spotify show: link, embed player, or spotify:show: uri
SPOTIFY_ID = re.compile(r"(?:open\.spotify\.com/(?:embed/)?show/|spotify:show:)([A-Za-z0-9]{22})")
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


def spotify_ok(url: str, name: str, fetch=_page, min_sim: float = 0.5) -> bool:
    """The show page's <title> is "<show> | Listen to the Podcast on Spotify" (oEmbed gives the latest episode, not the show)."""
    m = re.search(r"<title>([^<|]*)", fetch(url))
    return similar(name, unescape(m.group(1)).strip()) >= min_sim if m else False


def spotify_candidates(*texts: str) -> list[str]:
    """Spotify show URLs named in the given texts (site html, raw feed xml), most-mentioned first. A feed's item notes
    also advertise other shows, so every candidate still has to pass spotify_ok before it is used."""
    counts: dict[str, int] = {}
    for t in texts:
        for sid in SPOTIFY_ID.findall(t or ""):
            counts[sid] = counts.get(sid, 0) + 1
    return [f"https://open.spotify.com/show/{sid}" for sid, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


_DDG = {"blocked": False}


def spotify_search(name: str, fetch=None) -> list[str]:
    """Opt-in: Spotify show urls from a free DuckDuckGo HTML search. After one rate-limit page ("anomaly") no more searches
    are made in this process. Candidates still have to pass the Spotify title check."""
    import urllib.parse

    from .links import _text

    if _DDG["blocked"]:
        return []
    q = urllib.parse.urlencode({"q": f"site:open.spotify.com/show {name} podcast"}).encode()
    page = (fetch or _text)("https://html.duckduckgo.com/html/", data=q)
    if "anomaly" in page.lower():
        _DDG["blocked"] = True
        return []
    return spotify_candidates(urllib.parse.unquote(page))


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


def discover(src: dict, get=_get, fetch=_page, parse=None, ytsearch: bool = False, search=None) -> dict:
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
    html = ""
    if found["site"]:
        try:
            html = fetch(found["site"])
        except Exception:
            pass
        if not found["youtube"] and not is_host(found["site"]) and (url := _first(YT_CHANNEL, html)):
            try:
                found["youtube"] = url if youtube_ok(url, src["name"], fetch) else ""
            except Exception:
                pass
    if not found["spotify"]:  # the site's links, then the feed itself (show-notes boilerplate often links the show)
        try:
            raw = fetch(src["rss"]) if src.get("rss") else ""
        except Exception:
            raw = ""
        cands = spotify_candidates(html, raw)
        if not cands and search:  # nothing on the site or in the feed: web search, opt-in
            try:
                cands = search(src["name"])
            except Exception:
                cands = []
        for url in cands[:6]:
            try:
                if spotify_ok(url, src["name"], fetch, min_sim=0.8):  # stricter than 0.5: "Lenny's Reads" is not "Lenny's Podcast"
                    found["spotify"] = url
                    break
            except Exception:
                pass
    if ytsearch and not found["youtube"]:
        try:
            found["youtube"] = yt_channel_search(src["name"])
        except Exception:
            pass
    return found


def fill(sources: Path, links_path: Path, only: set[str] | None = None, discover_fn=discover, ytsearch: bool = False,
         websearch: bool = False) -> dict:
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
        opts = {**({"ytsearch": True} if ytsearch else {}), **({"search": spotify_search} if websearch else {})}
        found = discover_fn(src, **opts)
        got = [f for f in todo if found.get(f)]
        for f in got:
            row[f] = found[f]
        changed[sid] = got
    save(links_path, data)
    return changed


# --- verify -----------------------------------------------------------------------------------------------------------

class Blocked(Exception):
    """YouTube answered with a rate-limit / consent / bot-check signal: stop asking."""


def _yt_get(url: str, timeout: float = 20) -> str:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
                                               "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "consent.youtube.com" in r.geturl():
                raise Blocked("redirected to the consent page")
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 403):
            raise Blocked(f"HTTP {exc.code}") from None
        raise
    if "unusual traffic" in body[:20000].lower():
        raise Blocked("bot check page")
    return body


def channel_id(url: str, fetch=_yt_get) -> str:
    """UC id from /channel/UC..., else read it from the channel page (@handle and /c/ urls)."""
    if m := re.search(r"/channel/(UC[\w-]{22})", url):
        return m.group(1)
    html = fetch(url)
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html)
         or re.search(r'"(?:channelId|externalId|browseId)":"(UC[\w-]{22})"', html)
         or re.search(r'channel_id=(UC[\w-]{22})', html))
    if not m:
        raise ValueError("no channel id on the page")
    return m.group(1)


def channel_titles(cid: str, fetch=_yt_get, limit: int = 15) -> tuple[str, list[str]]:
    """(channel name, titles of the latest videos) from the channel's RSS feed."""
    xml = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    entries = re.findall(r"<entry>(.*?)</entry>", xml, re.S)
    head = xml.split("<entry>", 1)[0]
    name = unescape(m.group(1)).strip() if (m := re.search(r"<title>([^<]*)", head)) else ""
    out = []
    for entry in entries[:limit]:
        if m := re.search(r"<title>([^<]*)", entry):
            out.append(unescape(m.group(1)).strip())
    return name, out


def classify_youtube(video_titles: list[str], feed_titles: list[str], need: int = 2, min_sim: float = 0.6) -> tuple[str, str]:
    """(status, evidence). ok: >= `need` recent videos are titled like a recent feed episode. mismatch: both lists are
    long enough to judge and fewer match. unreachable: too little data to call it either way."""
    hits = [v for v in video_titles if feed_titles and max(similar(v, f) for f in feed_titles) >= min_sim]
    ev = f"{len(hits)}/{len(video_titles)} recent videos match {len(feed_titles)} feed titles"
    if len(hits) >= need:
        return "ok", ev + f" (e.g. {hits[0][:50]!r})"
    if len(video_titles) < need or len(feed_titles) < need:
        return "unreachable", ev + "; too few titles to judge"
    return "mismatch", ev + (f" (videos like {video_titles[0][:50]!r})" if video_titles else "")


def name_like(show: str, channel: str) -> bool:
    """Channel name and show name share every word of one of them ("Apple" / "Apple Events", "Sourcery with Molly O'Shea" /
    "Sourcery"). Stricter than similar(): "Supply Chain Now" is not "Supply Chained"."""
    def words(t: str) -> set[str]:
        return {w for w in re.sub(r"[^a-z0-9]+", " ", t.lower()).split() if w not in {"the", "show", "podcast", "podcasts", "with", "from"}}
    a, b = words(show), words(channel)
    return bool(a and b) and (a <= b or b <= a)


def verify_youtube(url: str, feed_titles: list[str], fetch=_yt_get, show_name: str = "") -> tuple[str, str, bool]:
    """(status, evidence, name_ok). name_ok: the channel is named like the show, so a title mismatch alone (YouTube retitles
    episodes, posts clips) is not proof the channel is wrong; --fix keeps such channels."""
    try:
        chan, vids = channel_titles(channel_id(url, fetch), fetch)
    except Blocked:
        raise
    except Exception as exc:
        return "unreachable", f"{type(exc).__name__}: {str(exc)[:60]}", False
    status, ev = classify_youtube(vids, feed_titles)
    name_ok = bool(show_name) and name_like(show_name, chan)
    ev += f"; channel {chan!r}" + (" is named like the show" if name_ok else "")
    return status, ev, name_ok


def verify_spotify(url: str, name: str, fetch=_page) -> tuple[str, str]:
    try:
        m = re.search(r"<title>([^<|]*)", fetch(url))
    except Exception as exc:
        return "unreachable", f"{type(exc).__name__}: {str(exc)[:60]}"
    title = unescape(m.group(1)).strip() if m else ""
    if not title:
        return "unreachable", "no <title> on the page"
    return ("ok" if similar(name, title) >= 0.5 else "mismatch"), f"page title {title[:50]!r}"


def feed_titles_of(src: dict, parse=None, limit: int = 40) -> list[str]:
    if parse is None:
        import feedparser
        parse = feedparser.parse
    return [e.get("title", "") for e in parse(src["rss"]).entries[:limit] if e.get("title")]


def verify(sources: Path, links_path: Path, only: set[str] | None = None, yt_fetch=_yt_get, page_fetch=_page, parse=None,
           pause: float = 0.0, sleep=None) -> list[dict]:
    """Check every set youtube and spotify value. Returns rows {id, name, field, value, status, evidence}. After the first
    YouTube block signal no more YouTube requests are made; the remaining channels are reported unreachable."""
    import time

    sleep = sleep or time.sleep
    srcs = {s.get("id"): s for s in (yaml.safe_load(sources.read_text()) or {}).get("sources", [])}
    rows, blocked = [], ""
    for sid, row in load(links_path).items():
        if only and sid not in only:
            continue
        for field in ("youtube", "spotify"):
            val = row.get(field) or ""
            if not val:
                continue
            rec = {"id": sid, "name": row.get("name", sid), "field": field, "value": val}
            if field == "youtube":
                if blocked:
                    rec.update(status="unreachable", evidence=f"skipped after YouTube block ({blocked})")
                elif sid not in srcs:
                    rec.update(status="unreachable", evidence="no entry in sources.yaml to read feed titles from")
                else:
                    try:
                        titles = feed_titles_of(srcs[sid], parse)
                        sleep(pause)
                        rec["status"], rec["evidence"], rec["name_ok"] = verify_youtube(val, titles, yt_fetch, rec["name"])
                    except Blocked as exc:
                        blocked = str(exc)
                        rec.update(status="unreachable", evidence=f"YouTube block signal: {blocked}; stopped asking")
                    except Exception as exc:
                        rec.update(status="unreachable", evidence=f"feed: {type(exc).__name__}: {str(exc)[:60]}")
            else:
                rec["status"], rec["evidence"] = verify_spotify(val, row.get("name", sid), page_fetch)
            rows.append(rec)
    return rows


def apply_fix(links_path: Path, results: list[dict]) -> list[dict]:
    """Blank the values whose status is mismatch (never unreachable), except a YouTube channel named like the show: its titles
    differ but nothing says it is the wrong channel. Returns the rows blanked."""
    bad = [r for r in results if r["status"] == "mismatch" and not r.get("name_ok")]
    if bad:
        data = load(links_path)
        for r in bad:
            data[r["id"]][r["field"]] = ""
        save(links_path, data)
    return bad
