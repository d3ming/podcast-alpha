import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, source_id TEXT, title TEXT, url TEXT,
  published_at TEXT, transcript_source TEXT, processed_at REAL
);
CREATE TABLE IF NOT EXISTS claims (
  episode_id TEXT, claim TEXT, tickers TEXT, created_at REAL
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

    def add_claims(self, episode_id: str, claims: list[dict]) -> None:
        self.db.executemany(
            "INSERT INTO claims VALUES (?,?,?,?)",
            [(episode_id, c["claim"], json.dumps(c.get("ticker", [])), time.time()) for c in claims],
        )
        self.db.commit()

    def recent_claims(self, days: int = 90, exclude_episode: str | None = None) -> list[str]:
        cutoff = time.time() - days * 86400
        rows = self.db.execute(
            "SELECT claim FROM claims WHERE created_at>=? AND episode_id IS NOT ?", (cutoff, exclude_episode)
        ).fetchall()
        return [r[0] for r in rows]
