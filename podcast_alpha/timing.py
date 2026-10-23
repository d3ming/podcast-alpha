"""Per-stage timings in manifest.json (fetch, transcribe, select, report, publish) and a cross-run trend table."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STAGES = ("fetch", "transcribe", "select", "report", "publish")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply(manifest: dict, stage: str, event: str, at: str | None = None) -> dict:
    if stage not in STAGES or event not in ("start", "end"):
        raise ValueError(f"stage must be one of {STAGES} and event start|end")
    t = manifest.setdefault("timing", {})
    s = t.setdefault(stage, {})
    s[event] = at or now_iso()
    if event == "start":
        s.pop("end", None), s.pop("seconds", None)
    elif s.get("start"):
        s["seconds"] = round((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds())
    starts = [v["start"] for k, v in t.items() if k in STAGES and v.get("start")]
    ends = [v["end"] for k, v in t.items() if k in STAGES and v.get("end")]
    if starts and ends:
        t["e2e"] = {"start": min(starts), "end": max(ends),
                    "seconds": round((datetime.fromisoformat(max(ends)) - datetime.fromisoformat(min(starts))).total_seconds())}
    return manifest


def mark(run_dir: Path, stage: str, event: str, at: str | None = None) -> dict:
    """Read-modify-write manifest.json atomically (transcribe also rewrites it; it re-reads each loop)."""
    path = Path(run_dir) / "manifest.json"
    manifest = apply(json.loads(path.read_text()), stage, event, at)
    tmp = path.with_suffix(f".mark{os.getpid()}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    tmp.replace(path)
    return manifest


def fmt(sec) -> str:
    if sec is None:
        return "-"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def stats(runs: Path) -> str:
    rows = []
    for p in sorted(Path(runs).glob("*/manifest.json")):
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        t, rec = m.get("timing", {}), m.get("recall") or {}
        eps = m.get("episodes", [])
        rows.append([p.parent.name] + [fmt(t.get(s, {}).get("seconds")) for s in STAGES] + [fmt(t.get("e2e", {}).get("seconds")),
                    f"{sum(1 for e in eps if e.get('has_transcript'))}/{len(eps)}",
                    f"{rec['hits']}/{rec['tmtb']} ({rec['recall']:.0%})" if rec.get("tmtb") else "-"])
    head = ["run", *STAGES, "e2e", "transcribed", "recall"]
    widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths))
    return "\n".join([line(head), line(["-" * w for w in widths])] + [line(r) for r in rows]) + "\n"
