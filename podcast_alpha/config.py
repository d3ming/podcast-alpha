from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Source:
    id: str
    name: str
    tier: int
    rss: str | None = None
    youtube: str | None = None
    topics: list[str] = field(default_factory=list)


def load_sources(path: str | Path) -> tuple[list[Source], int]:
    data = yaml.safe_load(Path(path).read_text())
    sources = [Source(**s) for s in data["sources"]]
    for s in sources:
        if s.tier not in (1, 2, 3):
            raise ValueError(f"{s.id}: tier must be 1, 2 or 3")
        if not (s.rss or s.youtube):
            raise ValueError(f"{s.id}: needs rss or youtube")
    return sources, int(data.get("t3_weekly_cap", 2))
