from __future__ import annotations

import json
import re


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


def from_rss_tag(links: list[dict]) -> list[dict] | None:
    import requests

    for link in links or []:
        try:
            r = requests.get(link["url"], timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            continue
        kind = (link.get("type") or "").lower()
        segs = parse_json(r.text) if "json" in kind else parse_cues(r.text)
        if segs:
            return segs
    return None


def from_youtube(video_id: str) -> list[dict] | None:
    from youtube_transcript_api import YouTubeTranscriptApi

    import time

    for attempt in range(3):
        try:
            t = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US"])
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(5 * (attempt + 1))
    return [{"start": s.start, "speaker": None, "text": s.text.replace("\n", " ")} for s in t.snippets]


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2}


def title_match(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def find_youtube_mirror(source_name: str, title: str) -> str | None:
    """Many audio-first shows also post to YouTube; use its captions when titles match."""
    import subprocess

    res = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s",
                          f"ytsearch3:{source_name} {title}"], capture_output=True, text=True, timeout=120)
    for line in res.stdout.splitlines():
        vid, _, ytitle = line.partition("\t")
        if title_match(title, ytitle) >= 0.6:
            return vid
    return None


def from_whisper(audio_url: str | None) -> list[dict] | None:
    """Stub: faster-whisper fallback is not wired up in the MVP."""
    return None


def get_transcript(ep) -> tuple[list[dict] | None, str | None]:
    if ep.transcript_urls:
        segs = from_rss_tag(ep.transcript_urls)
        if segs:
            return segs, "podcast:transcript"
    if ep.youtube_id:
        segs = from_youtube(ep.youtube_id)
        if segs:
            return segs, "youtube-captions"
    if not ep.youtube_id:
        vid = find_youtube_mirror(ep.source_name, ep.title)
        segs = from_youtube(vid) if vid else None
        if segs:
            ep.youtube_id = vid
            return segs, "youtube-captions (mirror)"
    segs = from_whisper(ep.audio_url)
    if segs:
        return segs, "whisper"
    return None, None
