"""Pre-transcribe triage: deterministic metadata score; whisper only the top N, never dropping core-person episodes."""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEYWORDS = re.compile(r"\b(ceo|cfo|coo|cto|founder|co-founder|chairman|president|earnings|guidance|capex|ipo|"
                      r"interview|exclusive|ai|chips?|semis?|semiconductors?|fabs?|gpu|data cent(er|re)s?|cloud|fund|investor|strategy|"
                      r"asml|tsmc|nvidia|intel|amd|broadcom|cloudflare|hybrid bonding|euv|macro|fed|inflation)\b", re.I)


GUEST = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-z]{2,}\b(?:\s*(?:&|and|,)\s*[A-Z][a-z]+\s[A-Z][a-z]+|:|\s+on\s)")


EXEC = re.compile(r"\b(ceo|cfo|cto|coo|cio|cso|chief [a-z]+ officer|co-?founder|founder|president|chair(man|woman|person)?|"
                  r"managing partner|general partner|(?:a |the )?partner at [A-Z][\w&.-]+|head of [A-Z]\w+|"
                  r"portfolio manager|hedge fund|family office)\b", re.I)
INVESTORS = re.compile(r"\b(druckenmiller|buffett|munger|ackman|tepper|dalio|griffin|einhorn|loeb|icahn|marks|klarman|"
                       r"chanos|cathie wood|gurley|andreessen|horowitz|thiel|khosla|doerr|son masayoshi|masayoshi son|"
                       r"tiger global|coatue|sequoia|benchmark|a16z|kleiner|bridgewater|citadel|elliott)\b", re.I)
OFF_DOMAIN = re.compile(r"\b(psychic(ally)?|mystic|tarot|astrolog\w*|horoscope|medium|channel(l)?er|energy read\w*|"
                        r"spirit guides?|manifest(ing|ation)|soul)\b", re.I)
CLIP_WORDS = re.compile(r"\b(clip|clips|shorts?|highlights?|excerpt|teaser|trailer|preview|recap|best of)\b", re.I)
STOP = set("the and for with from that this what why how are was its it's his her their our you your into about over "
           "just can't cant will has have not new all one out".split())


def text_of(e: dict) -> str:
    return f"{e.get('title', '')} {(e.get('description') or '')[:600]}"


def exec_guest(e: dict) -> str | None:
    t = text_of(e)
    m = EXEC.search(t) or INVESTORS.search(t)
    return m.group(0) if m else None


def duration(e: dict) -> int | None:
    if e.get("duration"):
        return int(e["duration"])
    return int(e["transcript_minutes"]) * 60 if e.get("transcript_minutes") else None


def topic_words(e: dict, names: set[str]) -> set[str]:
    words = set(re.findall(r"[a-z0-9$]+", e.get("title", "").lower())) - STOP - names
    return {w for w in words if len(w) > 2 and not w.isdigit()}


def guests(e: dict, people_idx) -> set[str]:
    tag = (e.get("source_tag") or "").lower()
    title = e.get("title", "").lower()
    return {n for n, _ in people_idx if f"person:{n}" in tag or n in title}


def find_clips(eps: list[dict], people_idx, ratio: float = 0.6, max_clip_min: int = 70) -> dict[str, str]:
    """{clip_id: full_id}: same guest (or >=2 shared topic words) and clearly shorter than the full episode."""
    out = {}
    for a in eps:
        da = duration(a)
        ga = guests(a, people_idx)
        for b in eps:
            if a is b or b["episode_id"] in out or a.get("source") == b.get("source"):
                continue
            db = duration(b)
            gb = guests(b, people_idx)
            shared_guest = bool(ga & gb)
            names = {w for n in ga | gb for w in n.split()}
            shared_topic = len(topic_words(a, names) & topic_words(b, names))
            if not (shared_guest and shared_topic >= 1) and shared_topic < 3:
                continue
            short = (da and db and da <= ratio * db and da <= max_clip_min * 60) or \
                    (CLIP_WORDS.search(a.get("title", "")) and not CLIP_WORDS.search(b.get("title", "")))
            if short:
                out[a["episode_id"]] = b["episode_id"]
                break
    return out


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
        s += 75 if pri == "core" else 30
        why.append(f"person search ({pri})")
    named = sorted({p for n, p in people_idx if n in text})
    if named and not pri:
        s += 40 if "core" in named else 20
        why.append(f"names a {'core' if 'core' in named else 'watch'} person")
    tier = e.get("tier") or 3
    s += {1: 50, 2: 15}.get(tier, 0)
    why.append(f"tier {tier}")
    inv = INVESTORS.search(text_of(e))
    ex = exec_guest(e)
    if inv:
        s += 60
        why.append(f"famous investor/firm ({inv.group(0).lower()})")
    elif ex:
        s += 80 if tier == 1 else 40
        why.append(f"exec guest ({ex.lower()}){' on tier-1 show' if tier == 1 else ''}")
    if OFF_DOMAIN.search(text_of(e)):
        s -= 150
        why.append("off-domain (" + OFF_DOMAIN.search(text_of(e)).group(0).lower() + ")")
    if GUEST.search(e.get("title", "")):
        s += 20
        why.append("named guest")
    kw = sorted({m.group(0).lower() for m in KEYWORDS.finditer(e.get("title", ""))})
    if kw:
        s += min(30, 10 * len(kw))
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


def backfill(eps: list[dict], run_dir) -> None:
    """Old manifests lack description: read it from the episode .md."""
    from pathlib import Path

    for e in eps:
        if e.get("description") or not run_dir or not e.get("file"):
            continue
        p = Path(run_dir) / e["file"]
        if p.exists():
            m = re.search(r"## Description\n\n(.*?)(?:\n## |\Z)", p.read_text(), re.S)
            e["description"] = m.group(1).strip()[:1500] if m else ""


def triage(manifest: dict, people, top: int, now: datetime | None = None, is_news=lambda e: False,
           run_dir=None) -> list[str]:
    """Annotate every episode with triage {score, reasons, rank, whisper}; return ids picked for whisper."""
    if now is None:
        try:
            now = datetime.fromisoformat(manifest["run_at"])
        except (KeyError, ValueError):
            now = datetime.now(timezone.utc)
    idx = person_index(people)
    eps = manifest.get("episodes", [])
    backfill(eps, run_dir)
    clips = find_clips(eps, idx)
    for e in eps:
        sc, why = score(e, idx, now, is_news)
        if e["episode_id"] in clips:
            sc -= 200
            why.append(f"clip of {clips[e['episode_id']]}")
        e["triage"] = {"score": sc, "reasons": why}
    seen_show: dict[str, int] = {}
    for e in sorted(eps, key=lambda e: (-e["triage"]["score"], e["episode_id"])):  # diversity: 2nd+ ep of a show
        k = (e.get("source") or "").lower()
        n = seen_show.get(k, 0)
        seen_show[k] = n + 1
        if n and k:
            e["triage"]["score"] -= 25 * n
            e["triage"]["reasons"].append(f"same show #{n + 1}")
    need = sorted((e for e in eps if not e.get("has_transcript") and e.get("audio_url")),
                  key=lambda e: (-e["triage"]["score"], e["episode_id"]))
    picked = [e for e in need[:top]] + [e for e in need[top:] if core(e) and not e.get("rerun")
                                         and e["episode_id"] not in clips
                                         and not any(r.startswith("off-domain") for r in e["triage"]["reasons"])]
    ids = {e["episode_id"] for e in picked}
    for rank, e in enumerate(need, 1):
        e["triage"].update(rank=rank, whisper=e["episode_id"] in ids)
    manifest["triage"] = {"top": top, "candidates": len(need), "whisper": len(ids), "clips": clips}
    return [e["episode_id"] for e in need if e["episode_id"] in ids]
