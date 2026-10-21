from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, source_id TEXT, title TEXT, url TEXT,
  published_at TEXT, transcript_source TEXT, processed_at REAL
);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def seen(self, episode_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM episodes WHERE id=?", (episode_id,)).fetchone() is not None

    def add_episode(self, ep, transcript_source: str | None) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO episodes VALUES (?,?,?,?,?,?,?)",
            (ep.id, ep.source_id, ep.title, ep.url, ep.published_at.isoformat(), transcript_source, time.time()),
        )
        self.db.commit()
        return cur.rowcount == 1

    def set_transcript(self, episode_id: str, transcript_source: str) -> None:
        self.db.execute("UPDATE episodes SET transcript_source=? WHERE id=?", (transcript_source, episode_id))
        self.db.commit()

    def known_since(self, since) -> list[SimpleNamespace]:
        rows = self.db.execute("SELECT id, title FROM episodes WHERE published_at >= ? AND source_id NOT LIKE 'person:%'",
                               (since.isoformat(),)).fetchall()
        return [SimpleNamespace(id=i, title=t) for i, t in rows]
