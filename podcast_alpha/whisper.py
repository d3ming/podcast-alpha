from __future__ import annotations

import os
import tempfile

MODEL = os.environ.get("PA_WHISPER_MODEL", "base.en")
_model = None


class Budget:
    def __init__(self, minutes: float, per_episode: float = 30):
        self.left, self.per_episode, self.used = minutes * 60, per_episode * 60, 0.0

    def take(self) -> float:
        return min(self.left, self.per_episode)

    def spend(self, seconds: float) -> None:
        self.left -= seconds
        self.used += seconds


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


def transcribe(audio_url: str | None, budget: Budget | None) -> tuple[list[dict] | None, float]:
    if not audio_url or not budget or budget.take() < 60:
        return None, 0.0
    import requests

    limit = budget.take()
    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        with requests.get(audio_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        f.flush()
        segs, info = model().transcribe(f.name, language="en", beam_size=1, vad_filter=True)
        out = segments_until(segs, limit)
    used = min(limit, info.duration)
    budget.spend(used)
    return out or None, used
