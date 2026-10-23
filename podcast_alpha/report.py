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
    if kind == "selected":
        bad = [e["episode_id"] for e in data["episodes"] if e.get("fallback_text") and "fallback" not in e["why"].lower()]
        if bad:
            raise RankedError(f"selected: fallback_text picks must say 'fallback' in why: {bad}")
    if kind == "report":
        ids = [e["episode_id"] for e in data["essential"]]
        missing = [e["episode_id"] for e in data["episodes"] if e["episode_id"] not in ids]
        if missing:
            raise RankedError(f"report: episodes not in essential: {missing}")
        from .links import bad_url
        for e in data["essential"] + data["episodes"]:
            if e.get("listen_url") and (why := bad_url(e["listen_url"])):
                raise RankedError(f"report: {e['episode_id']} listen_url is {why}: {e['listen_url']}")
    return data


def cite(c: dict, url: str) -> str:
    where = ", ".join(x for x in (c["speaker"], ts_link(url, c["timestamp"]) if c["timestamp"] is not None else "") if x)
    read = f" (our read: {c['our_read']})" if c.get("our_read") else ""
    return f"- {c['text']} [{where}]{read}"


def attach_links(data: dict, run_dir: Path | None) -> dict:
    """Fill youtube_url / listen_url on essential and episodes from selected.json; never audio or tracking."""
    from .links import bad_url
    sel = {}
    if run_dir and (run_dir / "selected.json").exists():
        sel = {e["episode_id"]: e for e in json.loads((run_dir / "selected.json").read_text())["episodes"]}
    eps = {e["episode_id"]: e for e in data["episodes"]}
    for e in data["essential"] + data["episodes"]:
        s, ep = sel.get(e["episode_id"], {}), eps.get(e["episode_id"], {})
        for k in ("youtube_url", "listen_url"):
            if not e.get(k) and (v := s.get(k) or ep.get(k)):
                e[k] = v
        if not e.get("listen_url") and ep.get("url") and not bad_url(ep["url"]):
            e["listen_url"] = ep["url"]
    return data


def best_link(e: dict) -> str | None:
    """YouTube first, then Apple/Spotify/episode page (listen_url), then a non-audio url."""
    from .links import bad_url
    for u in (e.get("youtube_url"), e.get("listen_url"), e.get("url")):
        if u and not bad_url(u):
            return u
    return None


def essential_line(i: int, e: dict) -> str:
    head = f"[{e['headline']}]({u})" if (u := best_link(e)) else e["headline"]
    return f"{i}. **{head}** {e['show']}, with {e['guests']}"


def render(data: dict) -> str:
    lines = [f"# {data['title']}", "", "## Key Insights", ""] + [f"- {k}" for k in data["key_insights"]]
    lines += ["", "## Essential Episodes", ""]
    lines += [essential_line(i, e) for i, e in enumerate(data["essential"], 1)]
    for ep in data["episodes"]:
        head = f"[{ep['headline']}]({u})" if (u := best_link(ep)) else ep["headline"]
        lines += ["", "---", "", f"## {head}", "", "### THE SIGNAL", ""]
        lines += [cite(c, best_link(ep) or ep["url"]) for c in ep["signal"]]
        if ep.get("interests"):
            lines += ["", f"**Interests to note:** {ep['interests']}"]
        for n, s in enumerate(ep["sections"], 1):
            lines += ["", f"### {n:02d} {s['title']}", "", s["body"]]
            lines += [cite(c, best_link(ep) or ep["url"]) for c in s.get("claims", [])]
    lines += ["", "_AI-assisted summaries of public episodes. Verify before relying on them. Not investment advice._"]
    return "\n".join(lines) + "\n"
