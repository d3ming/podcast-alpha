import json
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "ranked.schema.json"
FORBIDDEN = ("holding", "weight", "market_pct", "folio")


class RankedError(ValueError):
    pass


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    validate(data)
    return data


def validate(data: dict) -> list[str]:
    """Raises RankedError on schema or tier-rule violations; returns spread warnings."""
    schema = json.loads(SCHEMA_PATH.read_text())
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(map(str, exc.absolute_path)) or "root"
        raise RankedError(f"schema: {where}: {exc.message}") from None
    eps = data["episodes"]
    ranks = [e["rank"] for e in eps]
    if ranks != list(range(1, len(eps) + 1)):
        raise RankedError("ranks must run 1..n in order")
    t3 = [e for e in eps if e["tier"] == 3]
    if len(t3) > 2:
        raise RankedError(f"tier 3 has {len(t3)} episodes; the cap is 2")
    if any(e["tier"] == 3 and e["rank"] <= 3 for e in eps):
        raise RankedError("tier 3 can never be a top pick (ranks 1-3)")
    blob = json.dumps(data).lower()
    for word in FORBIDDEN:
        if f'"{word}' in blob:
            raise RankedError(f"output must not contain holdings data ({word!r})")
    warnings = []
    scores = sorted(e["score"] for e in eps)
    if len(scores) >= 5:
        if sum(s <= 4 for s in scores) < 0.3 * len(scores):
            warnings.append("spread: fewer than 30% of episodes score 4 or less")
        if max(scores) < 8:
            warnings.append("spread: no episode scores 8 or more")
        if len(set(scores)) <= 2:
            warnings.append("spread: scores are clustered")
    return warnings
