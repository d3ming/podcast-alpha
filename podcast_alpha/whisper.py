from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile

MODEL = os.environ.get("PA_WHISPER_MODEL", "base.en")
YTDLP_PACE = ["--sleep-requests", "2", "--sleep-interval", "10", "--max-sleep-interval", "30", "--limit-rate", "1M"]
_model = None


class Budget:
    def __init__(self, minutes: float, per_episode: float | None = None):
        self.left, self.used = minutes * 60, 0.0
        self.per_episode = per_episode * 60 if per_episode else None

    def take(self) -> float:
        return min(self.left, self.per_episode or self.left)

    def spend(self, seconds: float) -> None:
        self.left -= seconds
        self.used += seconds


def available() -> bool:
    from importlib.util import find_spec

    return find_spec("faster_whisper") is not None


def model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 1))
    return _model


def segments_until(segs, limit: float) -> list[dict]:
    out = []
    for s in segs:
        if s.start >= limit:
            break
        out.append({"start": s.start, "speaker": None, "text": s.text.strip()})
    return out


def transcribe_file(path: str, budget: Budget) -> tuple[list[dict] | None, float]:
    limit = budget.take()
    segs, info = model().transcribe(path, language="en", beam_size=1, vad_filter=True)
    out = segments_until(segs, limit)
    used = min(limit, info.duration)
    budget.spend(used)
    return out or None, used


def transcribe(audio_url: str | None, budget: Budget | None) -> tuple[list[dict] | None, float]:
    if not audio_url or not budget or budget.take() < 60:
        return None, 0.0
    import requests

    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        with requests.get(audio_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        f.flush()
        return transcribe_file(f.name, budget)


def ytdlp_audio_cmd(video_id: str, out: str) -> list[str]:
    return [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "-f", "bestaudio", *YTDLP_PACE, "-o", out,
            f"https://www.youtube.com/watch?v={video_id}"]


def transcribe_youtube(video_id: str, budget: Budget | None) -> tuple[list[dict] | None, float]:
    if not budget or budget.take() < 60 or not available():
        return None, 0.0
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(ytdlp_audio_cmd(video_id, os.path.join(d, "a.%(ext)s")), check=True, timeout=3600)
        return transcribe_file(glob.glob(os.path.join(d, "a.*"))[0], budget)
