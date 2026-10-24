from __future__ import annotations

import glob
import os
import platform
import subprocess
import sys
import tempfile

YTDLP_PACE = ["--sleep-requests", "2", "--sleep-interval", "10", "--max-sleep-interval", "30", "--limit-rate", "1M"]
DOWNLOAD_SECONDS = int(os.environ.get("PA_DOWNLOAD_SECONDS", "900"))  # a trickling server must not hold the queue
DEFAULT_THREADS = 4  # faster-whisper CPU threads; PA_WHISPER_THREADS overrides
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


DEFAULT_MODELS = {"mlx": "mlx-community/whisper-large-v3-turbo", "faster": "base.en"}
MODULES = {"mlx": "mlx_whisper", "faster": "faster_whisper"}
ALIASES = {"faster-whisper": "faster", "faster_whisper": "faster", "mlx-whisper": "mlx"}
BACKEND_CHOICES = ["auto", "mlx", "faster-whisper", "faster"]
PREFETCH = os.environ.get("PA_PREFETCH", "1") != "0"  # download the next episode while the current one transcribes


def has_module(name: str) -> bool:
    from importlib.util import find_spec

    return find_spec(name) is not None


def backend() -> str:
    """PA_WHISPER_BACKEND=mlx|faster|auto. Auto: MLX on Apple silicon when mlx_whisper imports, else faster-whisper."""
    choice = os.environ.get("PA_WHISPER_BACKEND", "auto").strip().lower()
    choice = ALIASES.get(choice, choice)
    if choice in MODULES:
        return choice
    if sys.platform == "darwin" and platform.machine() == "arm64" and has_module(MODULES["mlx"]):
        return "mlx"
    return "faster"


def available() -> bool:
    return has_module(MODULES[backend()])


def model_name(be: str | None = None) -> str:
    return os.environ.get("PA_WHISPER_MODEL") or DEFAULT_MODELS[be or backend()]


def short_model(name: str | None = None) -> str:
    """'mlx-community/whisper-large-v3-turbo' -> 'large-v3-turbo'; 'base.en' stays."""
    base = (name or model_name()).rstrip("/").rsplit("/", 1)[-1]
    return base.removeprefix("whisper-")


def label(used: float, extra: str = "") -> str:
    """transcript_source label: backend model and audio minutes, so the source is auditable."""
    return f"whisper {short_model()} ({round(used / 60)} min{extra})"


def threads() -> int:
    env = os.environ.get("PA_WHISPER_THREADS")
    return max(1, int(env)) if env else min(DEFAULT_THREADS, os.cpu_count() or 1)


def model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(model_name("faster"), device="cpu", compute_type="int8", cpu_threads=threads())
    return _model


def audio_seconds(path: str) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def segments_until(segs, limit: float) -> list[dict]:
    out = []
    for s in segs:
        if s.start >= limit:
            break
        out.append({"start": s.start, "speaker": None, "text": s.text.strip()})
    return out


def trim_audio(path: str, limit: float) -> str | None:
    """Copy the first `limit` seconds of path to a temp file with ffmpeg; None if that fails (caller uses the full file)."""
    import tempfile

    fd, out = tempfile.mkstemp(suffix=".wav", prefix="pa-trim-")
    os.close(fd)
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-t", str(limit), "-ac", "1", "-ar", "16000", out],
                           capture_output=True, timeout=1800)
        if r.returncode == 0 and os.path.getsize(out) > 0:
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.unlink(out)
    except OSError:
        pass
    return None


def run_mlx(path: str, limit: float | None = None) -> tuple[list, float]:
    """mlx-whisper on the Apple GPU. Returns (segments with .start/.text, audio seconds)."""
    from types import SimpleNamespace

    if sys.platform == "darwin" and "/opt/homebrew/bin" not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + "/opt/homebrew/bin"  # ffmpeg/ffprobe for a daemonized run with a bare PATH
    import mlx_whisper

    duration = audio_seconds(path)
    src, trimmed = path, None
    if limit is not None and duration and limit < duration:
        trimmed = trim_audio(path, limit)  # mlx transcribes the whole file; cut first so the budget bounds GPU time
        src = trimmed or path
    try:
        res = mlx_whisper.transcribe(src, path_or_hf_repo=model_name("mlx"), language="en",
                                     condition_on_previous_text=False)  # no carry-over: stops hallucination loops on long audio
    finally:
        if trimmed:
            try:
                os.unlink(trimmed)
            except OSError:
                pass
    segs = [SimpleNamespace(start=s["start"], end=s["end"], text=s["text"]) for s in res.get("segments", [])]
    return segs, duration or (segs[-1].end if segs else 0.0)


def run_faster(path: str) -> tuple[list, float]:
    segs, info = model().transcribe(path, language="en", beam_size=1, vad_filter=True)
    return segs, info.duration


def transcribe_file(path: str, budget: Budget) -> tuple[list[dict] | None, float]:
    limit = budget.take()
    segs, duration = run_mlx(path, limit) if backend() == "mlx" else run_faster(path)
    out = segments_until(segs, limit)
    used = min(limit, duration)
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


class Prefetch:
    """Download audio_url to a temp file in a background thread. result() joins and re-raises the download's own
    DownloadError, so a failed prefetch is handled exactly like a failed inline download."""

    def __init__(self, audio_url: str):
        import threading

        f = tempfile.NamedTemporaryFile(suffix=".audio", delete=False)
        self.url, self.path, self.error = audio_url, f.name, None
        self._thread = threading.Thread(target=self._run, args=(f,), daemon=True)
        self._thread.start()

    def _run(self, f) -> None:
        try:
            with f:
                download(self.url, f)
        except BaseException as exc:  # surfaced by result(), in the transcribe loop's own error handling
            self.error = exc

    def result(self) -> str:
        self._thread.join()
        if self.error:
            raise self.error
        return self.path

    def cleanup(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


def transcribe_prefetched(pf: Prefetch, budget: Budget | None) -> tuple[list[dict] | None, float]:
    try:
        path = pf.result()
        if not budget or budget.take() < 60:
            return None, 0.0
        return transcribe_file(path, budget)
    finally:
        pf.cleanup()


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
