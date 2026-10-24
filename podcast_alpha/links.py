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

from .manifest import update as update_manifest

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


_SHOW_FILLER = {"the", "show", "podcast"}
_CLIP = re.compile(r"#shorts?\b|\bshorts?\b|\bclips?\b|\bhighlights?\b|\btrailer\b", re.I)
_NAME_STOP = {"the", "a", "an", "and", "or", "of", "on", "in", "at", "to", "for", "how", "why", "what", "when", "who", "is", "are",
              "new", "inside", "from", "with", "this", "that", "full", "episode", "interview", "part", "live", "special",
              "wall", "street", "silicon", "valley", "york", "white", "house", "capitol", "hill", "bay", "area"}
_GENERIC = _NAME_STOP | {"about", "billion", "million", "trillion", "still", "year", "years", "says", "said", "just", "next", "will",
                         "your", "their", "into", "over", "more", "most", "than", "talks", "founder", "chief", "founders", "future"}


def _show_core(name: str) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split() if w not in _SHOW_FILLER)


def _channel_matches_show(show: str, channel: str) -> bool:
    """Channel is the show's: fuzzy match, or one name is a short form of the other once 'the'/'show'/'podcast' are dropped."""
    if not show or not channel:
        return False
    if similar(show, channel) >= 0.5:
        return True
    a, b = _show_core(show), _show_core(channel)
    return len(min(a, b, key=len)) >= 3 and (a == b or a.startswith(b + " ") or b.startswith(a + " "))


def _name_pairs(title: str) -> set[str]:
    """Adjacent capitalised words ("Andy Jassy"), skipping pairs that include a stopword like 'The' or 'How'."""
    pairs = set()
    for run in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", title):
        words = run.split()
        pairs |= {f"{x} {y}".lower() for x, y in zip(words, words[1:]) if x.lower() not in _NAME_STOP and y.lower() not in _NAME_STOP}
    return pairs


def _distinctive(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in _GENERIC}


def _days_apart(a: str | None, b: str | None) -> int | None:
    try:
        return abs((date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days)
    except ValueError:
        return None


def yt_matches(ep: dict, vtitle: str, channel: str = "", published: str | None = None, max_days: int = 4) -> bool:
    """Strict: title close (or a guest named in the title, from the show's channel) and, if known, date within max_days.
    Third rule, only with BOTH the show's channel and a known date within max_days: the titles share a person-like name
    ("Andy Jassy") or 4+ distinctive words, the titles are at least half alike, and the video is not a clip/short. YouTube retitles episodes, so similarity alone misses real matches."""
    title, show = ep.get("title", ""), ep.get("show") or ep.get("source") or ""
    if _CLIP.search(vtitle) and not _CLIP.search(title):  # a clip or short is not the episode
        return False
    guests = [g["name"] if isinstance(g, dict) else str(g) for g in ep.get("guests") or []
              if not (isinstance(g, dict) and "host" in (g.get("role") or "").lower())]
    guests = [g for g in guests if g and len(g.split()) >= 2]
    chan_ok = bool(channel) and similar(show, channel) >= 0.5
    ok = similar(title, vtitle) >= 0.75 or (chan_ok and any(g.lower() in vtitle.lower() for g in guests))
    gap = _days_apart(published, ep.get("published")) if published and ep.get("published") else None
    if ok:
        return gap is None or gap <= max_days
    if gap is not None and gap <= max_days and _channel_matches_show(show, channel) and not _CLIP.search(vtitle) \
            and similar(title, vtitle) >= 0.5:
        return bool(_name_pairs(title) & _name_pairs(vtitle)) or len(_distinctive(title) & _distinctive(vtitle)) >= 4
    return False


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
    meta = yt_oembed(vid, fetch) if vid else None  # oEmbed has no upload date: only the caller's date for THIS video counts
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


def yt_ytsearch(ep: dict, fetch=_text, run=None) -> str | None:
    """Last resort, opt-in (Mac/residential IP; YouTube blocks servers): yt-dlp's ytsearch5. Results are only
    accepted through yt_verify (oEmbed title/channel match, then the upload date, looked up with a second yt-dlp call)."""
    import subprocess
    import sys

    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True).stdout)
    guests = " ".join(g["name"] for g in ep.get("guests") or [] if isinstance(g, dict) and "host" not in (g.get("role") or "").lower())
    q = f"{ep.get('show') or ep.get('source') or ''} {guests or ep.get('title', '')}".strip()
    out = run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(channel)s\t%(upload_date)s", f"ytsearch5:{q}"])
    for line in out.splitlines():
        vid, *_, up = (line.split("\t") + [""])[:4]
        vid, url = vid.strip(), f"https://www.youtube.com/watch?v={vid.strip()}"
        if not re.fullmatch(r"[\w-]{11}", vid) or not yt_verify(ep, url, ep.get("youtube_published"), fetch):
            continue
        if not re.fullmatch(r"\d{8}", up.strip()):  # flat search lists no upload date: look it up for the one candidate
            try:
                up = run([sys.executable, "-m", "yt_dlp", "--skip-download", "--print", "%(upload_date)s", url]).strip().splitlines()[-1]
            except Exception:
                up = ""
        if re.fullmatch(r"\d{8}", up.strip()):
            if (dated := yt_verify(ep, url, f"{up[:4]}-{up[4:6]}-{up[6:8]}", fetch)):
                return dated
            continue
        return url
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


def youtube(ep: dict, channel_id: str | None = None, fetch=_text, ytsearch: bool = False, run=None) -> str | None:
    """Order: known id, agent-supplied url, feed show notes, channel RSS, web search, then (ytsearch=True) yt-dlp search."""
    if ep.get("youtube_id"):
        return f"https://www.youtube.com/watch?v={ep['youtube_id']}"
    if ep.get("youtube_url") and (u := yt_verify(ep, ep["youtube_url"], ep.get("youtube_published"), fetch)):
        return u
    for step in (lambda: yt_show_notes(ep, fetch), (lambda: yt_channel_rss(ep, channel_id, fetch)) if channel_id else None,
                 lambda: yt_ddg(ep, fetch), (lambda: yt_ytsearch(ep, fetch, run)) if ytsearch else None):
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


def transcript_link(row: dict) -> str | None:
    """Hosted transcript for a manifest row: the recorded URL (any host, but never audio), else the YouTube video whose
    captions were used."""
    u = row.get("transcript_url") or ""
    if u.startswith(("http://", "https://")) and not u.lower().split("?")[0].endswith(AUDIO_EXT):
        return u
    if "youtube-captions" in (row.get("transcript_source") or "") and row.get("youtube_id"):
        return f"https://www.youtube.com/watch?v={row['youtube_id']}"
    return None


def transcript_label(url: str) -> str:
    return "YouTube captions" if "youtube.com/" in url or "youtu.be/" in url else "Publisher transcript"


def backfill_transcript_urls(run_dir: Path, sources: Path, parse=None) -> dict:
    """Older manifests did not record where a podcast:transcript came from. Re-read each show's feed, match the episode by
    title and store the transcript link. YouTube-caption rows need nothing (transcript_link derives them)."""
    if parse is None:
        import feedparser
        parse = feedparser.parse
    from .ingest import transcript_links

    man_p = run_dir / "manifest.json"
    rows = [r for r in json.loads(man_p.read_text()).get("episodes", [])
            if r.get("transcript_source") == "podcast:transcript" and not r.get("transcript_url")]
    import yaml
    srcs = {s["id"]: s for s in yaml.safe_load(sources.read_text()).get("sources", []) if s.get("id")}
    feeds, found = {}, {}
    for r in rows:
        src = srcs.get(r.get("source_tag"))
        if not src:
            continue
        try:
            entries = feeds.setdefault(src["rss"], parse(src["rss"]).entries)
        except Exception:
            continue
        best = max(entries, key=lambda e: similar(r["title"], e.get("title", "")), default=None)
        if best and similar(r["title"], best.get("title", "")) >= 0.9 and (links := transcript_links(best)):
            found[r["episode_id"]] = links[0]["url"]

    def apply(m):
        for row in m.get("episodes", []):
            if row["episode_id"] in found:
                row["transcript_url"] = found[row["episode_id"]]

    if found:
        update_manifest(man_p, apply)
    return {"missing": len(rows), "found": len(found)}


def sources_by_name(sources: Path) -> dict:
    import yaml
    data = yaml.safe_load(sources.read_text()) or {}
    return {s["name"].lower(): s for s in data.get("sources", []) if isinstance(s, dict) and s.get("name")}


def fill(run_dir: Path, sources: Path | None = None, get=_get, fetch=_text, ytsearch: bool = False, show_links: Path | None = None) -> dict:
    """Write youtube_url (verified or empty) and listen_url (non-YouTube fallback) into selected.json and the manifest.
    Returns {episode_id: (youtube_url, listen_url)}."""
    sel_p, man_p = run_dir / "selected.json", run_dir / "manifest.json"
    sel = json.loads(sel_p.read_text())
    man = json.loads(man_p.read_text()) if man_p.exists() else {"episodes": []}
    by_id = {e["episode_id"]: e for e in man.get("episodes", [])}
    srcs = sources_by_name(sources) if sources and sources.exists() else {}
    from .showlinks import by_name as show_links_by_name
    shows = show_links_by_name(show_links) if show_links else {}
    out, found = {}, {}
    for e in sel["episodes"]:
        m = by_id.get(e["episode_id"], {})
        ep = {**m, **e, "youtube_id": e.get("youtube_id") or m.get("youtube_id")}
        if not ep.get("description") and m.get("file") and (run_dir / m["file"]).exists():  # older manifests
            md = re.search(r"## Description\n\n(.*?)(?:\n## |\Z)", (run_dir / m["file"]).read_text(), re.S)
            ep["description"] = md.group(1) if md else ""
        src = srcs.get((e.get("show") or "").lower(), {})
        yt = youtube(ep, src.get("youtube_channel_id"), fetch, ytsearch)
        lu = e.get("listen_url") if e.get("listen_url") and not bad_url(e["listen_url"]) and not yt_id(e["listen_url"]) else None
        lu = lu or resolve(ep, src.get("rss"), get)
        if not lu and not yt:  # no episode page anywhere: the show's own Apple page (then site) beats nothing
            sl = shows.get((e.get("show") or "").lower(), {})
            lu = sl.get("apple") or sl.get("site") or None
        out[e["episode_id"]] = (yt, lu)
        for d in (e, m) if m else (e,):
            d.pop("youtube_url", None) if not yt else d.__setitem__("youtube_url", yt)
            if lu:
                d["listen_url"] = lu
        if m:
            found[e["episode_id"]] = (yt, lu)
    sel_p.write_text(json.dumps(sel, indent=1, ensure_ascii=False) + "\n")
    if man_p.exists():
        def apply(manifest):  # only the link fields, onto the fresh manifest (transcribe may be writing it)
            for row in manifest.get("episodes", []):
                if (hit := found.get(row["episode_id"])):
                    yt, lu = hit
                    row.pop("youtube_url", None) if not yt else row.__setitem__("youtube_url", yt)
                    if lu:
                        row["listen_url"] = lu

        update_manifest(man_p, apply)
    return out
