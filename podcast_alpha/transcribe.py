from __future__ import annotations

import json
import re
import threading


TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d+))?")
SPEAKER = re.compile(r"^([A-Z][\w .'-]{0,40}):\s+(.*)$")


def to_seconds(stamp: str) -> float:
    m = TS.match(stamp.strip())
    if not m:
        raise ValueError(stamp)
    h, mi, s, frac = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + (float("0." + frac) if frac else 0.0)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _seg(start, text, speaker=None):
    m = SPEAKER.match(text)
    if m and speaker is None:
        speaker, text = m.group(1), m.group(2)
    return {"start": start, "speaker": speaker, "text": text.strip()}


def parse_cues(raw: str) -> list[dict]:
    """VTT, SRT, or plain text where a timestamp line precedes each block."""
    segs, start, buf = [], None, []

    def flush():
        if start is not None and buf:
            segs.append(_seg(start, " ".join(buf)))

    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.isdigit():
            continue
        if TS.match(line):
            flush()
            start, buf = to_seconds(line.split("-->")[0]), []
        elif start is not None:
            buf.append(re.sub(r"<[^>]+>", "", line))
    flush()
    return [s for s in segs if s["text"]]


def parse_json(raw: str) -> list[dict]:
    data = json.loads(raw)
    items = data.get("segments", []) if isinstance(data, dict) else data
    return [_seg(float(i.get("startTime", i.get("start", 0))), i.get("body", i.get("text", "")), i.get("speaker"))
            for i in items if i.get("body") or i.get("text")]


def from_rss_tag(links: list[dict], used: list | None = None) -> list[dict] | None:
    import requests

    from .ingest import UA

    for link in links or []:
        try:
            r = requests.get(link["url"], timeout=30, headers=UA)
            r.raise_for_status()
        except requests.RequestException:
            continue
        kind = (link.get("type") or "").lower()
        segs = parse_json(r.text) if "json" in kind else parse_cues(r.text)
        if segs:
            if used is not None:
                used.append(link["url"])
            return segs
    return None


YT_BLOCKED = threading.Event()  # set once YouTube refuses this IP: more requests only prolong the block
_YT_NO_RETRY = {"TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable", "VideoUnplayable", "InvalidVideoId"}


def from_youtube(video_id: str) -> list[dict] | None:
    """Captions for a video. A block, or a video with no captions, ends the lookup at once; only odd errors are retried."""
    from youtube_transcript_api import YouTubeTranscriptApi

    import time

    if YT_BLOCKED.is_set():
        return None
    for attempt in range(3):
        try:
            t = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US"])
            break
        except Exception as exc:
            kind = type(exc).__name__
            if kind in ("IpBlocked", "RequestBlocked"):
                if not YT_BLOCKED.is_set():
                    print("[youtube] this IP is blocked by YouTube; skipping captions and mirror lookups for the rest of the run")
                YT_BLOCKED.set()
                return None
            if kind in _YT_NO_RETRY or attempt == 2:
                return None
            time.sleep(5 * (attempt + 1))
    return [{"start": s.start, "speaker": None, "text": s.text.replace("\n", " ")} for s in t.snippets]


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2}


def title_match(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def find_youtube_mirror(source_name: str, title: str) -> str | None:
    """Many audio-first shows also post to YouTube; use its captions when the title nearly matches, or loosely matches on a
    channel that looks like the show. Clips and shorts never count (a clip's captions are not the episode)."""
    import subprocess

    from .links import similar

    if YT_BLOCKED.is_set():
        return None
    res = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(channel)s",
                          f"ytsearch3:{source_name} {title}"], capture_output=True, text=True, timeout=120)
    for line in res.stdout.splitlines():
        vid, _, rest = line.partition("\t")
        ytitle, _, channel = rest.partition("\t")
        tm = title_match(title, ytitle)
        if re.search(r"\bclips?\b|\bshorts?\b|\bhighlights?\b", f"{channel} {ytitle}", re.I) and not re.search(r"\bclips?\b", title, re.I):
            continue  # an official clips channel or a short is not the full episode
        # the show's own channel needs a loose title match; a network or label's channel (Bloomberg Podcasts for Odd Lots) needs a near-exact one
        if tm >= 0.85 or (tm >= 0.6 and (not channel or channel in ("NA", "None") or similar(source_name, channel) >= 0.5)):
            return vid
    return None


DEFAULT_METHODS = ["podcast_transcript", "youtube_captions", "youtube_mirror", "whisper"]


MIN_TRANSCRIPT_CHARS = 5000


def transcript_chars(segs: list[dict] | None) -> int:
    return sum(len(s["text"]) + 1 for s in segs or [])


def is_fallback(segs: list[dict] | None, min_chars: int = MIN_TRANSCRIPT_CHARS) -> bool:
    return bool(segs) and transcript_chars(segs) < min_chars


def get_transcript(ep, budget=None) -> tuple[list[dict] | None, str | None]:
    for m in ep.methods or DEFAULT_METHODS:
        segs, label = None, m
        if m == "podcast_transcript" and ep.transcript_urls:
            used: list = []
            segs, label = from_rss_tag(ep.transcript_urls, used), "podcast:transcript"
            if segs and used:
                ep.transcript_url = used[0]
        elif m == "whisper" and ep.audio_url and budget:
            from .whisper import transcribe

            try:
                segs, used = transcribe(ep.audio_url, budget)
            except Exception as exc:
                print(f"[whisper] {ep.id}: {type(exc).__name__}")
                continue
            label = f"whisper ({round(used / 60)} min)"
        elif m == "youtube_audio" and ep.youtube_id:
            from .whisper import transcribe_youtube

            try:
                segs, used = transcribe_youtube(ep.youtube_id, budget)
            except Exception as exc:
                print(f"[whisper] {ep.id}: {type(exc).__name__}")
                continue
            label = f"whisper ({round(used / 60)} min, youtube audio)"
        elif m == "youtube_captions" and ep.youtube_id:
            segs, label = from_youtube(ep.youtube_id), "youtube-captions"
            if segs:
                ep.transcript_url = f"https://www.youtube.com/watch?v={ep.youtube_id}"
        elif m == "youtube_mirror" and not ep.youtube_id:
            vid = find_youtube_mirror(ep.source_name, ep.title)
            segs = from_youtube(vid) if vid else None
            if segs:
                ep.youtube_id, label = vid, "youtube-captions (mirror)"
                ep.transcript_url = f"https://www.youtube.com/watch?v={vid}"
        if segs:
            return segs, label
    return None, None
