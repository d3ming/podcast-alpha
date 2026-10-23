"""Pre-transcribe triage: deterministic metadata score; whisper only the top N, never dropping core-person episodes."""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEYWORDS = re.compile(r"\b(ceo|cfo|coo|cto|founder|co-founder|chairman|president|earnings|guidance|capex|ipo|"
                      r"interview|exclusive|ai|chips?|semis?|gpu|data cent(er|re)s?|cloud|fund|investor|strategy)\b", re.I)


GUEST = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-z]{2,}\b(?:\s*(?:&|and|,)\s*[A-Z][a-z]+\s[A-Z][a-z]+|:|\s+on\s)")


def person_index(people) -> list[tuple[str, str]]:
    out = []
    for p in people or []:
        for n in [p.name, *getattr(p, "aliases", [])]:
            out.append((n.lower(), p.priority))
    return out


def score(e: dict, people_idx: list[tuple[str, str]], now: datetime, is_news) -> tuple[int, list[str]]:
    s, why = 0, []
    tag = e.get("source_tag") or ""
    text = f"{e.get('title', '')} {e.get('source', '')}".lower()
    pri = next((p for n, p in people_idx if tag.lower() == f"person:{n}"), None)
    if pri:
        s += 100 if pri == "core" else 40
        why.append(f"person search ({pri})")
    named = sorted({p for n, p in people_idx if n in text})
    if named:
        s += 60 if "core" in named else 25
        why.append(f"names a {'core' if 'core' in named else 'watch'} person")
    tier = e.get("tier") or 3
    s += {1: 40, 2: 15}.get(tier, 0)
    why.append(f"tier {tier}")
    if GUEST.search(e.get("title", "")):
        s += 20
        why.append("named guest")
    kw = sorted({m.group(0).lower() for m in KEYWORDS.finditer(e.get("title", ""))})
    if kw:
        s += min(20, 5 * len(kw))
        why.append("keywords: " + ",".join(kw[:4]))
    try:
        age = (now - datetime.fromisoformat(str(e.get("published"))[:10]).replace(tzinfo=timezone.utc)).days
        if age <= 1:
            s += 10
            why.append("recent")
    except ValueError:
        pass
    if is_news(e):
        s -= 50
        why.append("news show")
    if e.get("rerun"):
        s -= 100
        why.append("rerun")
    return s, why


def core(e: dict) -> bool:
    return any("(core)" in r or "names a core" in r for r in e.get("triage", {}).get("reasons", []))


def triage(manifest: dict, people, top: int, now: datetime | None = None, is_news=lambda e: False) -> list[str]:
    """Annotate every episode with triage {score, reasons, rank, whisper}; return ids picked for whisper."""
    if now is None:
        try:
            now = datetime.fromisoformat(manifest["run_at"])
        except (KeyError, ValueError):
            now = datetime.now(timezone.utc)
    idx = person_index(people)
    eps = manifest.get("episodes", [])
    for e in eps:
        sc, why = score(e, idx, now, is_news)
        e["triage"] = {"score": sc, "reasons": why}
    need = sorted((e for e in eps if not e.get("has_transcript") and e.get("audio_url")),
                  key=lambda e: (-e["triage"]["score"], e["episode_id"]))
    picked = [e for e in need[:top]] + [e for e in need[top:] if core(e) and not e.get("rerun")]
    ids = {e["episode_id"] for e in picked}
    for rank, e in enumerate(need, 1):
        e["triage"].update(rank=rank, whisper=e["episode_id"] in ids)
    manifest["triage"] = {"top": top, "candidates": len(need), "whisper": len(ids)}
    return [e["episode_id"] for e in need if e["episode_id"] in ids]
