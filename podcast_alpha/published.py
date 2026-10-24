"""Copy a run's published outputs into published/<run>/, which is committed so cloud agents can read past reports.

Only our own writing and public metadata go there: the agent's JSON outputs, the rendered markdown, a slim episode
list and a claims.jsonl. Transcripts, show descriptions and anything from the reference newsletter stay in runs/.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "published"
FILES = ("selected.json", "report.json", "report.md", "ranked.json")
MAX_BYTES = 1_000_000  # a published file this large is almost certainly not a report; keep it out of git
EPISODE_KEYS = ("episode_id", "title", "source", "tier", "published", "url", "youtube_id", "transcript_url",
                "has_transcript", "transcript_source", "excluded")


def episodes(manifest: dict) -> list[dict]:
    """Episode metadata without descriptions or local paths."""
    return [{k: e[k] for k in EPISODE_KEYS if e.get(k) not in (None, "")} for e in manifest.get("episodes", [])]


def export(run_dir: str | Path, root: str | Path | None = None, name: str | None = None) -> tuple[Path, list[str]]:
    """Write published/<name>/ from the run folder (replacing what was there). Returns the folder and the files written."""
    from . import library

    run_dir = Path(run_dir).resolve()
    dest = Path(root or ROOT) / (name or run_dir.name)
    srcs = [run_dir / f for f in FILES] + sorted(run_dir.glob("digest-*.md"))
    srcs = [p for p in srcs if p.is_file()]
    if not srcs:
        raise FileNotFoundError(f"nothing published in {run_dir} yet (no report.json, ranked.json or digest)")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    written, skipped = [], []
    for p in srcs:
        if p.stat().st_size > MAX_BYTES:
            skipped.append(p.name)
            continue
        shutil.copy2(p, dest / p.name)
        written.append(p.name)
    man = run_dir / "manifest.json"
    if man.exists():
        (dest / "episodes.json").write_text(json.dumps(episodes(json.loads(man.read_text())), indent=1, ensure_ascii=False) + "\n")
        written.append("episodes.json")
    with tempfile.TemporaryDirectory() as d:  # same claim shape as `library export` (schema/library-claim.schema.json)
        db = Path(d) / "lib.sqlite"
        library.ingest_run(run_dir, db=db, transcripts=False)
        with (dest / "claims.jsonl").open("w") as out:
            for row in library.query_claims(db, limit=0):
                row.pop("transcript_path", None)  # a local path means nothing to a cloud agent
                row["run"] = dest.name
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    written.append("claims.jsonl")
    for s in skipped:
        print(f"[published] skipped {s}: over {MAX_BYTES // 1000} KB")
    return dest, written
