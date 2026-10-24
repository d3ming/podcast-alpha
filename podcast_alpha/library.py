"""Durable, queryable insight library: claims and transcripts from every run in one SQLite file.

Ingest reads run folders and never writes into them. Re-ingesting a run replaces that run's claims; episode rows are
merged across runs (a later run never erases a transcript, link or score an earlier run supplied).
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "library.sqlite"
CHUNK_SECONDS = 60
CHUNK_CHARS = 1200

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY, title TEXT, show TEXT, published TEXT, url TEXT, youtube_url TEXT,
  transcript_url TEXT, transcript_path TEXT, has_transcript INTEGER DEFAULT 0, tier INTEGER, score INTEGER,
  run TEXT, run_date TEXT, ingested_at TEXT);
CREATE INDEX IF NOT EXISTS episodes_run ON episodes(run);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY, episode_id TEXT NOT NULL, run TEXT NOT NULL, run_date TEXT, date TEXT,
  source TEXT NOT NULL CHECK (source IN ('report','ranked')), text TEXT NOT NULL, speaker TEXT, timestamp INTEGER,
  novelty INTEGER, verifiable INTEGER, score INTEGER);
CREATE INDEX IF NOT EXISTS claims_run ON claims(run);
CREATE INDEX IF NOT EXISTS claims_date ON claims(date);
CREATE INDEX IF NOT EXISTS claims_episode ON claims(episode_id);
CREATE TABLE IF NOT EXISTS claim_tickers (claim_id INTEGER NOT NULL, ticker TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS claim_tickers_t ON claim_tickers(ticker);
CREATE INDEX IF NOT EXISTS claim_tickers_c ON claim_tickers(claim_id);
CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, episode_id TEXT NOT NULL, ts INTEGER);
CREATE INDEX IF NOT EXISTS chunks_episode ON chunks(episode_id);
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(text, speaker, title, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='porter unicode61');
PRAGMA user_version = 1;
"""


class LibraryMissing(ValueError):
    """The library file does not exist yet (nothing ingested)."""


BUSY_MS = 30000


def connect(db: str | Path | None = None, write: bool = False) -> sqlite3.Connection:
    """Read commands open the file read-only and never touch the schema, so they do not need the write lock an
    ingest holds. Only the ingest path (write=True) creates the file and the schema."""
    path = Path(db or DEFAULT_DB)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=BUSY_MS / 1000)
        con.execute(f"PRAGMA busy_timeout={BUSY_MS}")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        return con
    if not path.is_file():
        raise LibraryMissing(f"no library at {path}; run `podcast-alpha library ingest runs/<name>` (or --all) first")
    con = sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True, timeout=BUSY_MS / 1000)
    con.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    con.row_factory = sqlite3.Row
    return con


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _day(value) -> str | None:
    m = re.match(r"\d{4}-\d{2}-\d{2}", value or "")
    return m.group(0) if m else None


def _ticker(t: str) -> str:
    t = t.strip()
    return t if t.lower().startswith("private:") else t.upper()


def ts_link(url: str | None, ts: int | None) -> str | None:
    """Deep link to a moment: YouTube watch URLs get &t=<s>s; anything else stays the page URL."""
    if not url:
        return None
    if ts is not None and "youtube.com/watch" in url:
        return f"{url}&t={int(ts)}s"
    if ts is not None and "youtu.be/" in url:
        return f"{url}{'&' if '?' in url else '?'}t={int(ts)}"
    return url


def _transcript_url(row: dict) -> str | None:
    from .links import transcript_link

    try:
        return transcript_link(row)
    except Exception:
        return None


def _seconds(stamp: str) -> int:
    sec = 0
    for part in stamp.split(":"):
        sec = sec * 60 + int(part)
    return sec


def chunk_transcript(md: str) -> list[tuple[int, str]]:
    """Split the '## Transcript' body of an episode file into ~60 s windows of (start_seconds, text)."""
    chunks, cur, start, size = [], [], None, 0
    for line in md.partition("## Transcript")[2].splitlines():
        m = re.match(r"^\[([\d:]+)\]\s*(.*)$", line)
        if not m:
            continue
        sec, text = _seconds(m.group(1)), re.sub(r"^(>>\s*)+", "", m.group(2)).strip()
        if cur and (sec - start >= CHUNK_SECONDS or size >= CHUNK_CHARS):
            chunks.append((start, " ".join(cur)))
            cur, start, size = [], None, 0
        if text:
            start = sec if start is None else start
            cur.append(text)
            size += len(text) + 1
    if cur:
        chunks.append((start or 0, " ".join(cur)))
    return chunks


def _cites(ep: dict):
    seen = set()
    for c in ep.get("signal", []):
        yield c
    for s in ep.get("sections", []):
        for c in s.get("claims", []):
            yield c


def ingest_run(run_dir: str | Path, db: str | Path | None = None, transcripts: bool = True) -> dict:
    """Replace everything the library holds for this run with the run's current contents. Returns counts."""
    run_dir = Path(run_dir).resolve()
    run = run_dir.name
    manifest = _read(run_dir / "manifest.json") or {}
    report = _read(run_dir / "report.json") or {}
    ranked = _read(run_dir / "ranked.json") or {}
    if not (manifest.get("episodes") or report or ranked):
        raise FileNotFoundError(f"nothing to ingest in {run_dir} (need manifest.json, report.json or ranked.json)")
    run_date = _day(report.get("date")) or _day(ranked.get("week_of")) or _day(manifest.get("run_at")) or _day(run)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    eps: dict[str, dict] = {}
    for row in manifest.get("episodes", []):
        yid = row.get("youtube_id")
        eps[row["episode_id"]] = {
            "title": row.get("title"), "show": row.get("source"), "published": _day(row.get("published")),
            "url": row.get("url"), "youtube_url": f"https://www.youtube.com/watch?v={yid}" if yid else None,
            "transcript_url": _transcript_url(row), "tier": row.get("tier"), "score": None,
            "file": row.get("file") if row.get("has_transcript") and not row.get("fallback_text") else None,
        }
    for e in ranked.get("episodes", []):
        d = eps.setdefault(e["episode_id"], {"file": None, "youtube_url": None})
        for k, v in (("title", e.get("title")), ("show", e.get("source")), ("url", e.get("url")), ("tier", e.get("tier")),
                     ("published", _day(e.get("published"))), ("transcript_url", e.get("transcript_url"))):
            d[k] = d.get(k) or v
        d["score"] = e.get("score")
    for e in report.get("episodes", []):
        d = eps.setdefault(e["episode_id"], {"file": None})
        for k, v in (("title", e.get("headline")), ("url", e.get("url")), ("youtube_url", e.get("youtube_url")),
                     ("transcript_url", e.get("transcript_url"))):
            if v and (k == "youtube_url" or not d.get(k)):
                d[k] = v
    for e in report.get("essential", []):
        d = eps.get(e["episode_id"])
        if d is not None:
            d["show"] = d.get("show") or e.get("show")

    claims = []  # (episode_id, source, text, speaker, timestamp, tickers, novelty, verifiable, score)
    for e in ranked.get("episodes", []):
        for c in e.get("claims", []):
            claims.append((e["episode_id"], "ranked", c["claim"], c.get("speaker"), c.get("timestamp"),
                           [_ticker(t) for t in c.get("ticker", [])], c.get("novelty"), c.get("verifiable"), e.get("score")))
    for e in report.get("episodes", []):
        seen = set()
        for c in _cites(e):
            if (c["timestamp"], c["text"]) not in seen:
                seen.add((c["timestamp"], c["text"]))
                claims.append((e["episode_id"], "report", c["text"], c.get("speaker"), c.get("timestamp"), [], None, None, None))

    con = connect(db, write=True)
    n_chunks = 0
    with con:
        old = [r[0] for r in con.execute("SELECT id FROM claims WHERE run=?", (run,))]
        con.executemany("DELETE FROM claims_fts WHERE rowid=?", [(i,) for i in old])
        con.executemany("DELETE FROM claim_tickers WHERE claim_id=?", [(i,) for i in old])
        con.execute("DELETE FROM claims WHERE run=?", (run,))
        for eid, d in eps.items():
            path = run_dir / d["file"] if d.get("file") else None
            has_t = bool(path and path.is_file())
            # Merge, never downgrade: a run that lists the episode without a transcript, link or score keeps what an
            # earlier run stored. Re-ingesting the same run writes the same values, so it stays idempotent.
            con.execute("INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET "
                        "title=COALESCE(excluded.title, title), show=COALESCE(excluded.show, show), "
                        "published=COALESCE(excluded.published, published), url=COALESCE(excluded.url, url), "
                        "youtube_url=COALESCE(excluded.youtube_url, youtube_url), "
                        "transcript_url=COALESCE(excluded.transcript_url, transcript_url), "
                        "transcript_path=COALESCE(excluded.transcript_path, transcript_path), "
                        "has_transcript=MAX(has_transcript, excluded.has_transcript), tier=COALESCE(excluded.tier, tier), "
                        "score=COALESCE(excluded.score, score), run=excluded.run, run_date=excluded.run_date, "
                        "ingested_at=excluded.ingested_at",
                        (eid, d.get("title"), d.get("show"), d.get("published"), d.get("url"), d.get("youtube_url"),
                         d.get("transcript_url"), str(path) if has_t else None, int(has_t), d.get("tier"), d.get("score"),
                         run, run_date, now))
            if has_t and transcripts:
                _drop_chunks(con, eid)
                for ts, text in chunk_transcript(path.read_text(errors="replace")):
                    cur = con.execute("INSERT INTO chunks(episode_id, ts) VALUES (?,?)", (eid, ts))
                    con.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cur.lastrowid, text))
                    n_chunks += 1
        for eid, source, text, speaker, ts, tickers, nov, ver, score in claims:
            d = eps.get(eid, {})
            cur = con.execute("INSERT INTO claims(episode_id, run, run_date, date, source, text, speaker, timestamp, novelty, "
                              "verifiable, score) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (eid, run, run_date, d.get("published") or run_date, source, text, speaker, ts, nov, ver, score))
            con.execute("INSERT INTO claims_fts(rowid, text, speaker, title) VALUES (?,?,?,?)",
                        (cur.lastrowid, text, speaker or "", d.get("title") or ""))
            con.executemany("INSERT INTO claim_tickers VALUES (?,?)", [(cur.lastrowid, t) for t in dict.fromkeys(tickers)])
    con.close()
    return {"run": run, "episodes": len(eps), "claims": len(claims), "chunks": n_chunks}


def _drop_chunks(con, episode_id: str) -> None:
    ids = [(r[0],) for r in con.execute("SELECT id FROM chunks WHERE episode_id=?", (episode_id,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", ids)
    con.execute("DELETE FROM chunks WHERE episode_id=?", (episode_id,))


def run_dirs(runs: str | Path) -> list[Path]:
    return sorted({p.parent for pat in ("manifest.json", "report.json", "ranked.json") for p in Path(runs).glob(f"*/{pat}")})


def fts_query(text: str) -> str:
    """User text to a safe FTS5 expression: "quoted phrases" stay phrases, other words are ANDed."""
    parts = [m.group(1) or m.group(2) for m in re.finditer(r'"([^"]+)"|([\w][\w.\-]*)', text)]
    parts = [p.replace('"', " ").strip() for p in parts if p and p.strip()]
    if not parts:
        raise ValueError(f"no searchable words in {text!r}")
    return " ".join(f'"{p}"' for p in parts)


def _claim_row(r: sqlite3.Row, tickers: list[str], matched_by: str | None = None) -> dict:
    yt = r["youtube_url"]
    return {
        "episode_id": r["episode_id"], "title": r["title"], "show": r["show"], "published": r["published"], "date": r["date"],
        "tier": r["tier"], "speaker": r["speaker"], "timestamp": r["timestamp"], "text": r["text"], "tickers": tickers,
        "novelty": r["novelty"], "verifiable": r["verifiable"], "source": r["source"], "run": r["run"],
        "score": r["score"], "url": r["url"], "youtube_url": yt, "transcript_url": r["transcript_url"],
        "link": ts_link(yt or r["url"], r["timestamp"]), "transcript_path": r["transcript_path"],
        "matched_by": matched_by,
    }


MIN_BARE_TICKER = 3


def ticker_text_pattern(symbol: str, dollar: bool) -> re.Pattern | None:
    """Case-sensitive whole-symbol match on the original claim text, for claims that carry no ticker tags.
    `$NOW` always matches `$NOW`. A bare symbol matches only upper-case and only when it has 3+ characters, so
    short symbols (AI, IT, A) and lower-case words ("now", "costs") never match; use `$AI` for those."""
    if symbol.lower().startswith("private:"):
        return None
    sym = re.escape(symbol)
    if dollar:
        return re.compile(rf"\${sym}(?![A-Za-z0-9])")
    if len(symbol) < MIN_BARE_TICKER:
        return None
    return re.compile(rf"(?<![A-Za-z0-9$]){sym}(?![A-Za-z0-9])")


def query_claims(db=None, ticker=None, person=None, text=None, since=None, until=None, limit=20) -> list[dict]:
    """Claims newest first (best match first with text).

    ticker: exact tag match (case-insensitive) on tagged claims. Claims with no tags at all (daily report claims) match
    only if their text names the symbol as a whole upper-case word (3+ chars) or as `$SYM`; see ticker_text_pattern.
    Each row's matched_by says which ("tag" or "text"); it is None when no ticker filter was given."""
    con = connect(db)
    where, args = [], []
    sql = ("SELECT c.*, e.title, e.show, e.url, e.youtube_url, e.transcript_url, e.transcript_path, e.published, e.tier "
           "FROM claims c LEFT JOIN episodes e ON e.episode_id=c.episode_id")
    if text:
        where.append("c.id IN (SELECT rowid FROM claims_fts WHERE claims_fts MATCH ?)")
        args.append(fts_query(text))
    pattern = None
    if ticker:
        dollar = ticker.strip().startswith("$")
        t = _ticker(ticker.strip().lstrip("$"))
        pattern = ticker_text_pattern(t, dollar)
        cond = "c.id IN (SELECT claim_id FROM claim_tickers WHERE ticker=? COLLATE NOCASE)"
        args.append(t)
        if pattern:  # LIKE only narrows candidates; the regex below decides
            cond += (" OR (NOT EXISTS (SELECT 1 FROM claim_tickers x WHERE x.claim_id=c.id) AND c.text LIKE ? ESCAPE '\\')")
            args.append("%" + ("$" if dollar else "") + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        where.append(f"({cond})")
    if person:
        where.append("(c.speaker LIKE ? OR e.title LIKE ?)")
        args += [f"%{person}%"] * 2
    if since:
        where.append("c.date >= ?")
        args.append(since)
    if until:
        where.append("c.date <= ?")
        args.append(until)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.date DESC, c.score DESC, c.novelty DESC, c.id" if not text else \
        " ORDER BY (SELECT rank FROM claims_fts WHERE claims_fts MATCH ? AND rowid=c.id), c.date DESC"
    if text:
        args.append(fts_query(text))
    out = []
    for r in con.execute(sql, args).fetchall():
        tk = [x[0] for x in con.execute("SELECT ticker FROM claim_tickers WHERE claim_id=? ORDER BY rowid", (r["id"],))]
        matched = None
        if ticker:
            if any(x.upper() == t.upper() for x in tk):
                matched = "tag"
            elif not tk and pattern and pattern.search(r["text"]):
                matched = "text"
            else:
                continue
        out.append(_claim_row(r, tk, matched))
        if limit and len(out) >= limit:
            break
    con.close()
    return out


def search_transcripts(db, text: str, since=None, until=None, person=None, limit=20) -> list[dict]:
    """Best-matching ~60 s transcript windows, with a snippet and a link to the moment."""
    con = connect(db)
    sql = ("SELECT k.episode_id, k.ts, snippet(chunks_fts, 0, '**', '**', ' ... ', 40) AS snip, e.title, e.show, e.url, "
           "e.youtube_url, e.transcript_url, e.transcript_path, e.published, e.tier FROM chunks_fts "
           "JOIN chunks k ON k.id=chunks_fts.rowid LEFT JOIN episodes e ON e.episode_id=k.episode_id WHERE chunks_fts MATCH ?")
    args = [fts_query(text)]
    if since:
        sql += " AND e.published >= ?"
        args.append(since)
    if until:
        sql += " AND e.published <= ?"
        args.append(until)
    if person:
        sql += " AND e.title LIKE ?"
        args.append(f"%{person}%")
    sql += " ORDER BY chunks_fts.rank"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    out = [{"episode_id": r["episode_id"], "title": r["title"], "show": r["show"], "published": r["published"],
            "tier": r["tier"], "timestamp": r["ts"], "snippet": r["snip"], "url": r["url"], "youtube_url": r["youtube_url"],
            "transcript_url": r["transcript_url"], "link": ts_link(r["youtube_url"] or r["url"], r["ts"]),
            "transcript_path": r["transcript_path"]} for r in con.execute(sql, args)]
    con.close()
    return out


def export_jsonl(db, out, since=None, until=None) -> int:
    """Write every claim (newest first) as one JSON object per line to the open file `out`."""
    rows = query_claims(db, since=since, until=until, limit=0)
    for r in rows:
        out.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def stats(db=None) -> dict:
    con = connect(db)
    one = lambda q: con.execute(q).fetchone()[0]  # noqa: E731
    out = {
        "episodes": one("SELECT COUNT(*) FROM episodes"), "episodes_with_transcript": one("SELECT COUNT(*) FROM episodes WHERE has_transcript=1"),
        "claims": one("SELECT COUNT(*) FROM claims"), "transcript_chunks": one("SELECT COUNT(*) FROM chunks"),
        "runs": [r[0] for r in con.execute("SELECT DISTINCT run FROM claims UNION SELECT DISTINCT run FROM episodes ORDER BY 1")],
        "claims_by_source": {r[0]: r[1] for r in con.execute("SELECT source, COUNT(*) FROM claims GROUP BY source")},
        "date_range": [x for x in con.execute("SELECT MIN(date), MAX(date) FROM claims").fetchone()],
        "top_tickers": [[r[0], r[1]] for r in con.execute(
            "SELECT ticker, COUNT(*) n FROM claim_tickers GROUP BY ticker ORDER BY n DESC, ticker LIMIT 10")],
    }
    con.close()
    p = Path(db or DEFAULT_DB)
    out["db_bytes"] = p.stat().st_size if p.exists() else 0
    return out
