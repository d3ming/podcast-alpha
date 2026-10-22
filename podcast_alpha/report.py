from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from .digest import ts_link
from .ranked import FORBIDDEN, RankedError

SCHEMA = Path(__file__).resolve().parent.parent / "schema"


def validate(data: dict, kind: str) -> dict:
    try:
        jsonschema.validate(data, json.loads((SCHEMA / f"{kind}.schema.json").read_text()))
    except jsonschema.ValidationError as exc:
        raise RankedError(f"{kind}: {'/'.join(map(str, exc.absolute_path)) or 'root'}: {exc.message}") from None
    text = json.dumps(data).lower()
    if any(f'"{w}' in text for w in FORBIDDEN):
        raise RankedError(f"{kind}: holdings-like fields are not allowed")
    if kind == "report":
        ids = [e["episode_id"] for e in data["essential"]]
        missing = [e["episode_id"] for e in data["episodes"] if e["episode_id"] not in ids]
        if missing:
            raise RankedError(f"report: episodes not in essential: {missing}")
    return data


def cite(c: dict, url: str) -> str:
    where = ", ".join(x for x in (c["speaker"], ts_link(url, c["timestamp"]) if c["timestamp"] is not None else "") if x)
    read = f" (our read: {c['our_read']})" if c.get("our_read") else ""
    return f"- {c['text']} [{where}]{read}"


def render(data: dict) -> str:
    lines = [f"# {data['title']}", "", "## Key Insights", ""] + [f"- {k}" for k in data["key_insights"]]
    lines += ["", "## Essential Episodes", ""]
    lines += [f"{i}. **{e['headline']}** {e['show']}, with {e['guests']}" for i, e in enumerate(data["essential"], 1)]
    for ep in data["episodes"]:
        lines += ["", "---", "", f"## [{ep['headline']}]({ep['url']})", "", "### THE SIGNAL", ""]
        lines += [cite(c, ep["url"]) for c in ep["signal"]]
        for n, s in enumerate(ep["sections"], 1):
            lines += ["", f"### {n:02d} {s['title']}", "", s["body"]]
            lines += [cite(c, ep["url"]) for c in s.get("claims", [])]
    lines += ["", "_AI-assisted summaries of public episodes. Verify before relying on them. Not investment advice._"]
    return "\n".join(lines) + "\n"
