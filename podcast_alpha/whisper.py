from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile

MODEL = os.environ.get("PA_WHISPER_MODEL", "base.en")
YTDLP_PACE = ["--sleep-requests", "2", "--sleep-interval", "10", "--max-sleep-interval", "30", "--limit-rate", "1M"]
DOWNLOAD_SECONDS = int(os.environ.get("PA_DOWNLOAD_SECONDS", "900"))  # a trickling server must not hold the queue
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


RETRIES = int(os.environ.get("PA_DOWNLOAD_RETRIES", "3"))  # extra attempts after the first
BACKOFF = float(os.environ.get("PA_DOWNLOAD_BACKOFF", "10"))  # seconds; doubles each attempt


class DownloadError(Exception):
    """Download failed after retries; .attempts and .errors describe each try."""

    def __init__(self, msg: str, attempts: int, errors: list[str], transient: bool):
        super().__init__(msg)
        self.attempts, self.errors, self.transient = attempts, errors, transient


def describe(exc: Exception) -> str:
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{type(exc).__name__} {code}" if code else type(exc).__name__


def transient(exc: Exception) -> bool:
    """Retry 5xx, 429, timeouts and connection drops; never 4xx like 404/403."""
    import requests

    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, requests.HTTPError):
        return code is not None and (code >= 500 or code == 429)
    return isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError,
                            TimeoutError, ConnectionError))


def download(audio_url: str, dest, retries: int | None = None, backoff: float | None = None, sleep=None, get=None) -> int:
    """Stream audio_url into the open file dest with retry+exponential backoff. Returns attempts used."""
    import time

    import requests

    from .ingest import UA

    retries = RETRIES if retries is None else retries
    backoff = BACKOFF if backoff is None else backoff
    sleep, get = sleep or time.sleep, get or requests.get
    errors = []
    for attempt in range(1, retries + 2):
        dest.seek(0), dest.truncate()
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        try:
            with get(audio_url, stream=True, timeout=60, headers=UA) as r:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    dest.write(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"download over {DOWNLOAD_SECONDS}s")
            dest.flush()
            return attempt
        except Exception as exc:
            errors.append(describe(exc))
            if not transient(exc) or attempt > retries:
                raise DownloadError(errors[-1], attempt, errors, transient(exc)) from exc
            sleep(backoff * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def transcribe(audio_url: str | None, budget: Budget | None) -> tuple[list[dict] | None, float]:
    if not audio_url or not budget or budget.take() < 60:
        return None, 0.0
    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        download(audio_url, f)
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
