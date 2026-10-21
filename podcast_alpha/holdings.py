"""Holdings are held in memory for the run only. They are never logged, printed or written to disk."""
import requests

FOLIO_URL = "https://alpha.mingdom.co/folio?scope=all&format=json"


def _weights(data) -> dict[str, float]:
    rows = (data.get("holdings") or []) if isinstance(data, dict) else data
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        t = r.get("ticker") or r.get("symbol")
        w = r.get("market_pct", r.get("weight"))
        if t and isinstance(w, (int, float)):
            out[str(t).upper()] = abs(float(w)) / 100
    return out


def fetch_weights(url: str = FOLIO_URL) -> dict[str, float]:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return _weights(r.json())
    except Exception:
        print("[holdings] unavailable; continuing without weighting")
        return {}


def relevance(score: float, tickers: list[str], weights: dict[str, float]) -> float:
    w = max((weights.get(t.upper(), 0.0) for t in tickers), default=0.0)
    return score * (1 + w)
