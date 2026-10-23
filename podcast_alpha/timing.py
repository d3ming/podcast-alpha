"""Per-stage timings in manifest.json (fetch, transcribe, select, report, publish) and a cross-run trend table."""
from __future__ import annotations

import json
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
        active, idle = split(t)
        t["e2e"].update(active_seconds=active, idle_seconds=idle)
    return manifest


def split(t: dict) -> tuple[int | None, int | None]:
    """(active seconds = sum of stage durations, idle seconds = select.start - transcribe.end, never negative)."""
    secs = [t[s]["seconds"] for s in STAGES if isinstance(t.get(s), dict) and t[s].get("seconds") is not None]
    active = sum(secs) if secs else None
    idle = None
    tr, se = t.get("transcribe", {}), t.get("select", {})
    if tr.get("end") and se.get("start"):
        idle = max(0, round((datetime.fromisoformat(se["start"]) - datetime.fromisoformat(tr["end"])).total_seconds()))
    return active, idle


def mark(run_dir: Path, stage: str, event: str, at: str | None = None) -> dict:
    """Locked read-modify-write of manifest.json (transcribe and others write it concurrently)."""
    from .manifest import update

    return update(Path(run_dir) / "manifest.json", lambda m: apply(m, stage, event, at))


def fmt(sec) -> str:
    if sec is None:
        return "-"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def stats(runs: Path) -> str:
    rows, last = [], None
    for p in sorted(Path(runs).glob("*/manifest.json")):
        try:
            m = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        last = (p.parent.name, m)
        t, rec = m.get("timing", {}), m.get("recall") or {}
        eps = m.get("episodes", [])
        active, idle = split(t)
        rows.append([p.parent.name] + [fmt(t.get(s, {}).get("seconds")) for s in STAGES] + [fmt(active), fmt(idle),
                    fmt(t.get("e2e", {}).get("seconds")),
                    f"{sum(1 for e in eps if e.get('has_transcript'))}/{len(eps)}",
                    f"{rec['hits']}/{rec['tmtb']} ({rec['recall']:.0%})" if rec.get("tmtb") else "-"])
    head = ["run", *STAGES, "active", "idle", "wall", "transcribed", "recall"]
    widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths))
    out = "\n".join([line(head), line(["-" * w for w in widths])] + [line(r) for r in rows]) + "\n"
    if last and isinstance(last[1].get("sources"), dict):
        bad = errored_sources(last[1]["sources"])
        out += f"source errors in {last[0]}: {'; '.join(bad) if bad else 'none'}\n"
    return out


def errored_sources(health: dict) -> list[str]:
    return [f"{k} ({v['error']})" for k, v in health.items() if v.get("error")]
