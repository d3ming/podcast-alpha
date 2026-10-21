import json
import re

from . import llm
from .tickers import find_tickers
from .transcribe import fmt_ts

EXTRACT_PROMPT = """You extract investor-relevant claims from a podcast transcript chunk.
Return JSON {"claims": [...]}; each claim has: ticker (list of tickers, may be empty), claim (one falsifiable
sentence), speaker (name or role if known, else "unknown"), timestamp (seconds from start, number),
novelty (0-3: 0 = widely known, 3 = new primary information), verifiable (0-3: 3 = checkable against
filings or public data). Skip ads, banter and generic opinions. At most 8 claims per chunk."""

NUMBER = re.compile(r"(\$\s?\d|\d+(\.\d+)?\s?(%|percent|billion|million|trillion|x\b|gigawatt|gw\b|bps|basis points))", re.I)
FORECAST = re.compile(r"\b(will|expect|guid|forecast|by 20\d\d|next year|next quarter|going to|double|triple|ramp)\w*", re.I)
FACTUAL = re.compile(r"\b(revenue|margin|capex|growth|shipments?|orders?|backlog|share|price|cost|demand|supply|capacity|contract|deal|raised|valuation|earnings|yield)\b", re.I)
AD = re.compile(r"\b(sponsor|promo code|brought to you by|sign up at|use code|subscribe)\b", re.I)


def sentences(segments: list[dict]):
    """Yields (start_seconds, speaker, sentence); sentences may span caption snippets."""
    text = " ".join(s["text"] for s in segments)
    if segments and text.count(".") < len(text.split()) / 60:
        yield from caption_windows(segments)
        return
    buf, start, speaker = [], None, None
    for seg in segments:
        parts = re.split(r"(?<=[.!?])\s+", seg["text"].strip())
        for i, part in enumerate(parts):
            if start is None:
                start, speaker = seg["start"], seg.get("speaker")
            buf.append(part)
            ends = i < len(parts) - 1 or re.search(r"[.!?]$", part)
            if ends or seg.get("speaker") != speaker:
                sent = " ".join(buf).strip()
                if len(sent.split()) >= 6:
                    yield start, speaker, sent
                buf, start = [], None


def caption_windows(segments: list[dict], words: int = 30):
    """Unpunctuated auto-captions get grouped into ~30-word windows instead of sentences."""
    start, buf = None, []
    for seg in segments:
        if start is None:
            start = seg["start"]
        buf += seg["text"].split()
        if len(buf) >= words:
            yield start, None, " ".join(buf)
            start, buf = None, []
    if buf and len(buf) >= 6:
        yield start, None, " ".join(buf)


def _overlap(a: str, b: str) -> float:
    wa = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if len(w) > 3}
    wb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if len(w) > 3}
    return len(wa & wb) / max(1, len(wa | wb))


def novelty(claim: str, prior: list[str]) -> int:
    best = max((_overlap(claim, p) for p in prior), default=0.0)
    return 3 if best < 0.15 else 2 if best < 0.3 else 1 if best < 0.5 else 0


def heuristic_claims(segments: list[dict], prior: list[str], limit: int = 40) -> list[dict]:
    out = []
    for start, speaker, s in sentences(segments):
        if AD.search(s) or len(s) > 400:
            continue
        tickers = find_tickers(s)
        has_num, has_fc, has_fact = bool(NUMBER.search(s)), bool(FORECAST.search(s)), bool(FACTUAL.search(s))
        if not ((tickers and (has_num or has_fc or has_fact)) or (has_num and has_fact)):
            continue
        out.append({
            "ticker": tickers, "claim": s, "speaker": speaker or "unknown",
            "timestamp": round(start), "novelty": novelty(s, prior + [c["claim"] for c in out]),
            "verifiable": min(3, int(has_num) * 2 + int(has_fact) + int(bool(tickers))),
        })
    out.sort(key=lambda c: (c["verifiable"] + c["novelty"] + 2 * min(len(c["ticker"]), 1)), reverse=True)
    return out[:limit]


def chunk(segments: list[dict], seconds: int = 900) -> list[list[dict]]:
    chunks, cur, edge = [], [], seconds
    for seg in segments:
        if seg["start"] >= edge and cur:
            chunks.append(cur)
            cur, edge = [], edge + seconds
        cur.append(seg)
    return chunks + [cur] if cur else chunks


def llm_claims(segments: list[dict], title: str) -> list[dict]:
    claims = []
    for part in chunk(segments):
        text = "\n".join(f"[{fmt_ts(s['start'])}] {s.get('speaker') or ''}: {s['text']}" for s in part)
        data = llm.complete_json(EXTRACT_PROMPT, f"Episode: {title}\n\n{text}") or {}
        for c in data.get("claims", []):
            c["timestamp"] = _ts_seconds(c.get("timestamp", part[0]["start"]))
            claims.append(c)
    return claims


def _ts_seconds(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    from .transcribe import to_seconds
    try:
        return int(to_seconds(str(v)))
    except ValueError:
        return 0


def extract(segments: list[dict], title: str, prior: list[str]) -> tuple[list[dict], str]:
    if llm.available_provider():
        try:
            return llm_claims(segments, title), "llm"
        except Exception as exc:
            print(f"[extract] LLM failed ({type(exc).__name__}); using heuristic")
    return heuristic_claims(segments, prior), "heuristic"
