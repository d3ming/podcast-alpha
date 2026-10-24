"""Weekly ranking plumbing: split a run into reader batches, then merge the readers' outputs into ranked.json.

Code does the mechanical parts (condensing, batching, dedupe, ordering, notes); the readers (agents following
prompts/rank.md) do the judgment (scores, claims).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ANCHOR_SECONDS = 30
RANK_DIR = "rank"


def _seconds(stamp: str) -> int:
    parts = [int(x) for x in stamp.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def condense(md: str, step: int = ANCHOR_SECONDS) -> str:
    """Keep the front matter and description; join the transcript into paragraphs that start with a [timestamp] every `step`
    seconds. Cuts reader input roughly in half without losing the timestamps claims must cite."""
    out, buf, last, in_body = [], [], -10**9, False
    for line in md.splitlines():
        m = re.match(r"\[(\d+:\d+(?::\d+)?)\]\s?(.*)", line.strip())
        if not m:
            if not in_body:
                out.append(line)
            continue
        in_body = True
        t = _seconds(m.group(1))
        if t - last >= step:
            if buf:
                out.append(" ".join(buf))
                buf = []
            buf.append(f"[{m.group(1)}]")
            last = t
        buf.append(m.group(2))
    if buf:
        out.append(" ".join(buf))
    return "\n".join(out)


def prep(run_dir: Path, batches: int = 4) -> dict:
    """Write rank/cond/<episode>.md and rank/batch-N-in.json (episodes with a real transcript, balanced by size)."""
    man = json.loads((run_dir / "manifest.json").read_text())
    rows = [e for e in man["episodes"] if e.get("has_transcript") and not e.get("fallback_text")]
    out = run_dir / RANK_DIR
    (out / "cond").mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda e: (-(e.get("transcript_chars") or 0), e["episode_id"]))
    groups: list[list[dict]] = [[] for _ in range(max(1, min(batches, len(rows) or 1)))]
    for i, e in enumerate(rows):  # deal largest first, snaking, so batches carry similar total text
        n = len(groups)
        groups[i % n if (i // n) % 2 == 0 else n - 1 - i % n].append(e)
    for k, group in enumerate(groups, 1):
        items = []
        for e in group:
            if not (run_dir / e["file"]).exists():
                print(f"[rank-prep] skip {e['episode_id']}: {e['file']} is missing")
                continue
            (out / "cond" / e["file"]).write_text(condense((run_dir / e["file"]).read_text()))
            items.append({"episode_id": e["episode_id"], "file": f"{RANK_DIR}/cond/{e['file']}", "title": e["title"],
                          "source": e["source"], "tier": e["tier"], "published": e["published"], "url": e["url"],
                          "minutes": e.get("transcript_minutes"), "transcript_source": e.get("transcript_source")})
        (out / f"batch-{k}-in.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    return {"transcribed": len(rows), "without_transcript": len(man["episodes"]) - len(rows), "batches": len(groups)}


def _fingerprint(run_dir: Path, row: dict) -> str | None:
    """Same transcript on two feeds -> same fingerprint. Samples three slices (25/50/75%) so a shared pre-roll or intro
    at the start does not make two different episodes look alike."""
    md = run_dir / row["file"]
    if not md.exists():
        return None
    body = md.read_text().partition("## Transcript")[2]
    text = re.sub(r"\W+", " ", re.sub(r"\[[\d:]+\]\s*", "", body).lower()).strip()
    if len(text) < 2000:
        return None
    n = len(text)
    return hashlib.sha1("|".join(text[int(n * f):int(n * f) + 300] for f in (0.25, 0.5, 0.75)).encode()).hexdigest()


def apply_tier3(ranked: list[dict]) -> tuple[list[dict], list[str]]:
    """prompts/rank.md: at most 2 tier-3 episodes, and none in ranks 1-3. The rule spans batches, so the merge enforces it."""
    t3 = [r for r in ranked if r["tier"] == 3]
    keep = {r["episode_id"] for r in t3[:2]}
    cut = [r["title"] for r in t3[2:]]
    rest = [r for r in ranked if r["tier"] != 3 or r["episode_id"] in keep]
    top = [r for r in rest if r["tier"] != 3][:3]
    moved = [r["title"] for r in rest[:3] if r["tier"] == 3]
    head_ids = {r["episode_id"] for r in top}
    out = top + [r for r in rest if r["episode_id"] not in head_ids]
    return out, cut + moved


def merge(run_dir: Path, ranked_by: str, week_of: str | None = None) -> dict:
    """Merge rank/batch-*-out.json into ranked.json data. Same transcript on two feeds keeps the higher-scored row (then better tier);
    order is score, tier, newest first (prompts/rank.md)."""
    man = json.loads((run_dir / "manifest.json").read_text())
    by_id = {e["episode_id"]: e for e in man["episodes"]}
    rows, dropped, unknown = [], [], []
    for f in sorted((run_dir / RANK_DIR).glob("batch-*-out.json")):
        for r in json.loads(f.read_text()):
            (rows if r["episode_id"] in by_id else unknown).append(r)
    if unknown:
        raise ValueError("readers returned ids not in the manifest: " + ", ".join(r["episode_id"] for r in unknown[:5]))
    seen_ids, rows2 = set(), []
    for r in rows:
        if r["episode_id"] not in seen_ids:
            seen_ids.add(r["episode_id"])
            rows2.append(r)
    best: dict[str, dict] = {}
    for r in sorted(rows2, key=lambda r: (-r["score"], r["tier"], r["episode_id"])):
        fp = _fingerprint(run_dir, by_id[r["episode_id"]]) or r["episode_id"]
        if fp in best:
            dropped.append(r["title"])
        else:
            best[fp] = r
    ranked = sorted(best.values(), key=lambda r: (-r["score"], r["tier"], "".join(chr(255 - ord(c)) for c in r["published"][:10]),
                                                  r["episode_id"]))
    ranked, cut = apply_tier3(ranked)
    eps = [{**{k: r[k] for k in ("episode_id", "title", "url", "source", "tier", "published", "score", "reason", "claims")},
            "rank": i} for i, r in enumerate(ranked, 1)]
    untranscribed = len(man["episodes"]) - len(by_id_with_text(man))
    notes = (f"{len(man['episodes'])} episodes fetched; {len(eps)} with transcripts are ranked and {untranscribed} had no usable "
             f"transcript and are not ranked, so audio-only shows are underrepresented."
             + (f" Same-transcript duplicates merged: {'; '.join(t[:60] for t in dropped)}." if dropped else "")
             + (f" Tier-3 cap (2, never top 3) removed or moved: {'; '.join(t[:60] for t in cut)}." if cut else ""))
    return {"week_of": week_of or min(e["published"][:10] for e in man["episodes"]), "run_dir": str(run_dir),
            "ranked_by": ranked_by, "notes": notes, "episodes": eps}


def by_id_with_text(man: dict) -> list[dict]:
    return [e for e in man["episodes"] if e.get("has_transcript") and not e.get("fallback_text")]
