"""Scoring with a forced spread.

Anchors (1-10): 9-10 = new primary information, checkable, material to a ticker;
5-6 = solid recap of known theses; 3 or lower = hype, listicle or unverifiable.
Batch distribution: top 10% score 8 or more, the median lands near 5, and at least 30% score 4 or less.
"""
import re

from .extract import NUMBER

TIER_POINTS = {1: 10.0, 2: 6.0, 3: 3.0}
GUEST = re.compile(r"(\bwith\b|\bw/|\bft\.?|\bfeat|\binterview|\||–|—| - |:)", re.I)


def _target(pct: float) -> float:
    if pct < 0.1:
        return 10 - 2 * pct / 0.1
    if pct < 0.5:
        return 8 - 3 * (pct - 0.1) / 0.4
    if pct < 0.7:
        return 5 - (pct - 0.5) / 0.2
    return 4 - 3 * (pct - 0.7) / 0.3


def force_spread(raw: list[float]) -> list[int]:
    """Maps raw values to 1-10 by rank so a batch can never cluster; ties share a score."""
    n = len(raw)
    if n == 0:
        return []
    if n == 1:
        return [5]
    order = sorted(range(n), key=lambda i: raw[i], reverse=True)
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and raw[order[j + 1]] == raw[order[i]]:
            j += 1
        p = ((i + j) / 2) / (n - 1)
        for k in range(i, j + 1):
            pct[order[k]] = p
        i = j + 1
    return [max(1, min(10, round(_target(p)))) for p in pct]


def claim_raw(c: dict, tier: int) -> float:
    specificity = min(3, len(NUMBER.findall(c["claim"])) + min(1, len(c.get("ticker", []))))
    return 2 * c.get("verifiable", 0) + 2 * c.get("novelty", 0) + specificity + TIER_POINTS[tier] / 5


def score_claims(batch: list[tuple[dict, int]]) -> None:
    scores = force_spread([claim_raw(c, t) for c, t in batch])
    for (c, _), s in zip(batch, scores):
        c["score"] = s


def episode_components(ep, claims: list[dict], minutes: float) -> dict:
    top = sorted(claims, key=lambda c: c.get("score", 0), reverse=True)[:5]
    avg = lambda k: sum(c.get(k, 0) for c in top) / len(top) if top else 0.0
    density = len(claims) / max(minutes / 60, 0.25)
    return {
        "tier": TIER_POINTS[ep.tier],
        "novelty": avg("novelty") / 3 * 10,
        "verifiability": avg("verifiable") / 3 * 10,
        "specificity": min(10.0, density / 2),
        "speaker_access": 7.0 if GUEST.search(ep.title) else 4.0,
    }


WEIGHTS = {"tier": 0.20, "novelty": 0.25, "verifiability": 0.20, "specificity": 0.20, "speaker_access": 0.15}
LABELS = {"tier": "source tier", "novelty": "novel claims", "verifiability": "checkable claims",
          "specificity": "dense with numbers and tickers", "speaker_access": "guest access"}


def episode_raw(comp: dict) -> float:
    return sum(WEIGHTS[k] * v for k, v in comp.items())


def reason(comp: dict, n_claims: int, transcript: bool) -> str:
    if not transcript:
        return "No transcript found, so it scores on source tier alone."
    if n_claims == 0:
        return "Transcript had no ticker-linked, checkable claims."
    ranked = sorted(comp, key=lambda k: WEIGHTS[k] * comp[k], reverse=True)
    weak = min(comp, key=comp.get)
    return f"Strongest on {LABELS[ranked[0]]} and {LABELS[ranked[1]]}; weakest on {LABELS[weak]} ({n_claims} claims)."


def score_episodes(rows: list[dict]) -> None:
    scores = force_spread([r["raw"] for r in rows])
    for r, s in zip(rows, scores):
        r["score"] = s
