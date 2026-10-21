from __future__ import annotations

import os

API = "https://api.supadata.ai/v1"
ENV = "SUPADATA_API_KEY"
UPLOAD = [(0.05, "hour"), (1, "today"), (7, "week"), (31, "month"), (366, "year")]


def key() -> str | None:
    return os.environ.get(ENV) or None


def get(path: str, **params) -> dict:
    import requests

    r = requests.get(f"{API}{path}", params=params, headers={"x-api-key": key() or ""}, timeout=60)
    r.raise_for_status()
    return r.json()


def upload_filter(days: float) -> str:
    return next((name for d, name in UPLOAD if days <= d), "all")


def search(query: str, days: float, limit: int = 20) -> list[dict]:
    data = get("/youtube/search", query=query, uploadDate=upload_filter(days), type="video", duration="long", limit=limit)
    return [r for r in data.get("results", []) if r.get("type", "video") == "video"]


def channel_videos(channel: str, limit: int = 8) -> list[str]:
    return get("/youtube/channel/videos", id=channel, limit=limit, type="video").get("videoIds", [])


def video(video_id: str) -> dict:
    return get("/youtube/video", id=video_id)


def to_segments(data: dict) -> list[dict] | None:
    content = data.get("content")
    if not isinstance(content, list):
        return None
    return [{"start": c.get("offset", 0) / 1000, "speaker": None, "text": c["text"].strip()} for c in content if c.get("text")] or None


def transcript(video_id: str) -> list[dict] | None:
    return to_segments(get("/youtube/transcript", videoId=video_id, lang="en"))
