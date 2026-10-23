"""Resolve a clickable listen/watch page per episode (free: iTunes Search/Lookup, YouTube ids). Never audio or tracking."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date
from html import unescape
from difflib import SequenceMatcher
from pathlib import Path

AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".mp4a")
TRACKING = ("podtrac.com", "chrt.fm", "pdst.fm", "pscrb.fm", "swap.fm", "byspotify.com", "traffic.megaphone.fm",
            "traffic.libsyn.com", "dcs.megaphone.fm", "op3.dev", "podscribe", "claritaspod", "arttrk.com", "pfx.vpixl.com",
            "mgln.ai", "rss.art19.com/episodes", "media.blubrry.com", "injector.simplecastaudio.com", "cdn.simplecast.com/audio",
            "anchor.fm/s/", "cloudfront.net", "buzzsprout.com/rss", "redirect.mp3", "audioboom.com/posts/.*\\.mp3")
PREFERRED = ("youtube.com/watch", "youtu.be/", "open.spotify.com/episode", "podcasts.apple.com/")
UA = {"User-Agent": "podcast-alpha/0.1 (+https://github.com/d3ming/podcast-alpha)"}


def bad_url(url: str | None) -> str | None:
    """Reason the URL can't be a listen link, or None if it's fine."""
    if not url or not re.match(r"^https?://", url):
        return "not an http(s) URL"
    low = url.lower()
    path = urllib.parse.urlparse(low).path
    if path.endswith(AUDIO_EXT) or ".mp3" in low:
        return "audio file"
    for t in TRACKING:
        if re.search(t, low):
            return f"tracking/CDN host ({t})"
    return None



def similar(a: str, b: str) -> float:
    a2, b2 = " ".join(re.sub(r"[^a-z0-9]+", " ", a.lower()).split()), " ".join(re.sub(r"[^a-z0-9]+", " ", b.lower()).split())
    if a2 and b2 and (a2 in b2 or b2 in a2):
        return 1.0
    return SequenceMatcher(None, a2, b2).ratio()


def _text(url: str, timeout: float = 15, data: bytes | None = None) -> str:
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


YT_ID = re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/live/)([A-Za-z0-9_-]{11})")


def yt_id(url: str | None) -> str | None:
    m = YT_ID.search(urllib.parse.unquote(url or ""))
    return m.group(1) if m else None


def yt_matches(ep: dict, vtitle: str, channel: str = "", published: str | None = None, max_days: int = 4) -> bool:
    """Strict: title close (or a guest named in the title, from the show's channel) and, if known, date within max_days."""
    title, show = ep.get("title", ""), ep.get("show") or ep.get("source") or ""
    guests = [g["name"] if isinstance(g, dict) else str(g) for g in ep.get("guests") or []
              if not (isinstance(g, dict) and "host" in (g.get("role") or "").lower())]
    guests = [g for g in guests if g and len(g.split()) >= 2]
    chan_ok = bool(channel) and similar(show, channel) >= 0.5
    ok = similar(title, vtitle) >= 0.75 or (chan_ok and any(g.lower() in vtitle.lower() for g in guests))
    if ok and published and ep.get("published"):
        try:
            ok = abs((date.fromisoformat(published[:10]) - date.fromisoformat(str(ep["published"])[:10])).days) <= max_days
        except ValueError:
            pass
    return ok


def yt_channel_rss(ep: dict, channel_id: str, fetch=_text) -> str | None:
    xml = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>([^<]+)", entry)
        vt = re.search(r"<title>([^<]*)", entry)
        pub = re.search(r"<published>([^<]+)", entry)
        if vid and vt and yt_matches(ep, unescape(vt.group(1)), ep.get("show", ""), pub and pub.group(1)):
            return f"https://www.youtube.com/watch?v={vid.group(1)}"
    return None


def yt_oembed(vid: str, fetch=_text) -> dict | None:
    try:
        return json.loads(fetch(f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={vid}"))
    except Exception:
        return None


def yt_verify(ep: dict, url: str, published: str | None = None, fetch=_text) -> str | None:
    """Agent- or search-supplied URL: keep it only if oEmbed title/channel match the episode."""
    vid = yt_id(url)
    meta = yt_oembed(vid, fetch) if vid else None
    if meta and yt_matches(ep, meta.get("title", ""), meta.get("author_name", ""), published):
        return f"https://www.youtube.com/watch?v={vid}"
    return None


def yt_ddg(ep: dict, fetch=_text) -> str | None:
    """Free DuckDuckGo HTML search; often rate-limited ("anomaly" page), then returns None."""
    guests = " ".join(g["name"] for g in ep.get("guests") or [] if isinstance(g, dict))
    q = f"site:youtube.com {ep.get('show', '')} {guests or ep.get('title', '')}"
    page = fetch("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": q}).encode())
    if "anomaly" in page.lower():
        return None
    seen = []
    for vid in YT_ID.findall(urllib.parse.unquote(page)):
        if vid not in seen:
            seen.append(vid)
    for vid in seen[:5]:
        if (u := yt_verify(ep, f"https://www.youtube.com/watch?v={vid}", fetch=fetch)):
            return u
    return None


YT_URL = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s\"'<>]*v=|live/|shorts/)|youtu\.be/)[\w-]{11}[^\s\"'<>]*")


def yt_show_notes(ep: dict, fetch=_text) -> str | None:
    """YouTube links the publisher put in the feed description/show notes; oEmbed-verified (notes often link other eps)."""
    seen = []
    for u in YT_URL.findall(ep.get("description") or ""):
        if (vid := yt_id(u)) and vid not in seen:
            seen.append(vid)
    for vid in seen[:5]:
        if (u := yt_verify(ep, f"https://www.youtube.com/watch?v={vid}", fetch=fetch)):
            return u
    return None


def youtube(ep: dict, channel_id: str | None = None, fetch=_text) -> str | None:
    """Order: known id, agent-supplied url, feed show notes, channel RSS, web search."""
    if ep.get("youtube_id"):
        return f"https://www.youtube.com/watch?v={ep['youtube_id']}"
    if ep.get("youtube_url") and (u := yt_verify(ep, ep["youtube_url"], ep.get("youtube_published"), fetch)):
        return u
    for step in (lambda: yt_show_notes(ep, fetch), (lambda: yt_channel_rss(ep, channel_id, fetch)) if channel_id else None,
                 lambda: yt_ddg(ep, fetch)):
        if step:
            try:
                if (u := step()):
                    return u
            except Exception:
                pass
    return None


def _get(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


def apple_episode(show: str, title: str, feed: str | None = None, get=_get) -> str | None:
    q = urllib.parse.urlencode({"term": show, "media": "podcast", "entity": "podcast", "limit": 10})
    shows = get(f"https://itunes.apple.com/search?{q}").get("results", [])
    if feed:
        shows.sort(key=lambda r: (r.get("feedUrl", "").rstrip("/") != feed.rstrip("/"), -similar(show, r.get("collectionName", ""))))
    else:
        shows.sort(key=lambda r: -similar(show, r.get("collectionName", "")))
    for s in shows[:3]:
        if not (feed and s.get("feedUrl", "").rstrip("/") == feed.rstrip("/")) and similar(show, s.get("collectionName", "")) < 0.6:
            continue
        eps = get(f"https://itunes.apple.com/lookup?id={s['collectionId']}&entity=podcastEpisode&limit=200").get("results", [])
        best = max((e for e in eps if e.get("wrapperType") == "podcastEpisode"),
                   key=lambda e: similar(title, e.get("trackName", "")), default=None)
        if best and similar(title, best.get("trackName", "")) >= 0.8 and not bad_url(best.get("trackViewUrl")):
            return best["trackViewUrl"].split("&uo=")[0]
    # show search missed (renamed shows): search the episode title directly
    q = urllib.parse.urlencode({"term": title[:100], "media": "podcast", "entity": "podcastEpisode", "limit": 10})
    for e in get(f"https://itunes.apple.com/search?{q}").get("results", []):
        feed_ok = feed and e.get("feedUrl", "").rstrip("/") == feed.rstrip("/")
        if similar(title, e.get("trackName", "")) >= 0.9 and (feed_ok or similar(show, e.get("collectionName", "")) >= 0.5) \
                and not bad_url(e.get("trackViewUrl")):
            return e["trackViewUrl"].split("&uo=")[0]
    return None


def resolve(ep: dict, feed: str | None = None, get=_get) -> str | None:
    """Non-YouTube listen page: existing Spotify/Apple page, Apple lookup, then feed episode page."""
    url = ep.get("url")
    if url and not bad_url(url) and any(p in url for p in PREFERRED[:3]):
        return url
    if url and "podcasts.apple.com/" in url and not bad_url(url):
        return url.split("&uo=")[0]
    try:
        apple = apple_episode(ep.get("show") or ep.get("source") or "", ep.get("title", ""), feed, get)
    except Exception:  # network errors fall through to the page link
        apple = None
    if apple:
        return apple
    return url if url and not bad_url(url) else None


def sources_by_name(sources: Path) -> dict:
    import yaml
    data = yaml.safe_load(sources.read_text()) or {}
    return {s["name"].lower(): s for s in data.get("sources", []) if isinstance(s, dict) and s.get("name")}


def fill(run_dir: Path, sources: Path | None = None, get=_get, fetch=_text) -> dict:
    """Write youtube_url (verified or empty) and listen_url (non-YouTube fallback) into selected.json and the manifest.
    Returns {episode_id: (youtube_url, listen_url)}."""
    sel_p, man_p = run_dir / "selected.json", run_dir / "manifest.json"
    sel = json.loads(sel_p.read_text())
    man = json.loads(man_p.read_text()) if man_p.exists() else {"episodes": []}
    by_id = {e["episode_id"]: e for e in man.get("episodes", [])}
    srcs = sources_by_name(sources) if sources and sources.exists() else {}
    out = {}
    for e in sel["episodes"]:
        m = by_id.get(e["episode_id"], {})
        ep = {**m, **e, "youtube_id": e.get("youtube_id") or m.get("youtube_id")}
        if not ep.get("description") and m.get("file") and (run_dir / m["file"]).exists():  # older manifests
            md = re.search(r"## Description\n\n(.*?)(?:\n## |\Z)", (run_dir / m["file"]).read_text(), re.S)
            ep["description"] = md.group(1) if md else ""
        src = srcs.get((e.get("show") or "").lower(), {})
        yt = youtube(ep, src.get("youtube_channel_id"), fetch)
        lu = e.get("listen_url") if e.get("listen_url") and not bad_url(e["listen_url"]) and not yt_id(e["listen_url"]) else None
        lu = lu or resolve(ep, src.get("rss"), get)
        out[e["episode_id"]] = (yt, lu)
        for d in (e, m) if m else (e,):
            d.pop("youtube_url", None) if not yt else d.__setitem__("youtube_url", yt)
            if lu:
                d["listen_url"] = lu
    sel_p.write_text(json.dumps(sel, indent=1, ensure_ascii=False) + "\n")
    if man_p.exists():
        man_p.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
    return out
