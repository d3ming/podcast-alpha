from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DISCOVERY = {"rss", "supadata", "ytdlp"}
METHODS = {"podcast_transcript", "whisper", "supadata", "youtube_captions", "youtube_mirror"}
DEFAULTS = {"preferred_source": "rss", "fallback": ["supadata", "ytdlp"], "transcripts": ["podcast_transcript", "whisper", "supadata"]}


@dataclass
class Source:
    id: str
    name: str
    tier: int
    rss: str | None = None
    youtube: str | None = None
    topics: list[str] = field(default_factory=list)
    preferred_source: str = "rss"
    fallback: list[str] = field(default_factory=list)
    transcripts: list[str] = field(default_factory=list)

    @property
    def order(self) -> list[str]:
        have = {"rss": bool(self.rss), "supadata": bool(self.youtube), "ytdlp": bool(self.youtube)}
        return [s for s in dict.fromkeys([self.preferred_source, *self.fallback]) if have[s]]


def check(owner: str, cfg: dict) -> None:
    bad = ({cfg.get("preferred_source")} | set(cfg.get("fallback", []))) - DISCOVERY - {"itunes"}
    bad |= set(cfg.get("transcripts", [])) - METHODS
    if bad:
        raise ValueError(f"{owner}: unknown source or method {sorted(bad)}")


def load_sources(path: str | Path) -> tuple[list[Source], int]:
    data = yaml.safe_load(Path(path).read_text())
    defaults = {**DEFAULTS, **data.get("defaults", {})}
    sources = [Source(**{**defaults, **s}) for s in data["sources"]]
    for s in sources:
        if s.tier not in (1, 2, 3):
            raise ValueError(f"{s.id}: tier must be 1, 2 or 3")
        if not (s.rss or s.youtube):
            raise ValueError(f"{s.id}: needs rss or youtube")
        check(s.id, vars(s))
    return sources, int(data.get("t3_weekly_cap", 2))
