from __future__ import annotations

import re

from .links import transcript_label
from .transcribe import fmt_ts
from .triage import named_guests


def ts_link(url: str, seconds: int) -> str:
    if "youtube.com/watch" in url:
        return f"[{fmt_ts(seconds)}]({url}&t={int(seconds)}s)"
    return fmt_ts(seconds)


def link(title: str, url: str) -> str:
    return f"[{title}]({url})" if url.strip() else title


def show_line(links: dict | None) -> str:
    """`Show: Site · YouTube · Apple · Spotify` from show_links.yaml; blank fields are left out."""
    parts = [f"[{label}]({links[key]})" for key, label in (("site", "Site"), ("youtube", "YouTube"), ("apple", "Apple"), ("spotify", "Spotify"))
             if links and links.get(key)]
    return "Show: " + " · ".join(parts) if parts else ""


_HOST_ROLE = re.compile(r"\b(co-?hosts?|hosts?|moderators?|interviewers?|anchors?)\b", re.I)


def people(e: dict, hosts: set[str] | None = None) -> dict[str, str]:
    """{lower-cased name: display name} for the guests of a ranked episode: named in the title, or a claim's speaker
    ("Sundar Pichai (Alphabet CEO)"). Unnamed roles ("host", "guest analyst") and speakers whose full label gives a host
    role ("Chamath Palihapitiya (host)") are skipped; their lower-cased names are added to `hosts` when it is passed."""
    found = {n.lower(): n for n in named_guests(e.get("title", ""), e.get("source", ""))}
    for c in e.get("claims", []):
        raw = c.get("speaker") or ""
        name = raw.split("(")[0].strip()
        if _HOST_ROLE.search(raw):
            if hosts is not None and name:
                hosts.add(name.lower())
            continue
        if len(name.split()) >= 2 and name[0].isupper() and not name.lower().startswith(("host", "guest")):
            found.setdefault(name.lower(), name)
    return found


def repeat_notes(eps: list[dict]) -> dict[int, list[str]]:
    """{index: notes} for episodes whose guest also appears in an earlier episode of the list: "Also #3: another interview
    with Sundar Pichai". A name who is a host in any listed episode never counts as a guest."""
    hosts: set[str] = set()
    per = [people(e, hosts) for e in eps]
    seen: dict[str, list[int]] = {}
    notes: dict[int, list[str]] = {}
    for i, (e, ppl) in enumerate(zip(eps, per)):
        for key, name in ppl.items():
            if key in hosts:
                continue
            if seen.get(key):
                ranks = ", ".join(f"#{r}" for r in seen[key])
                notes.setdefault(i, []).append(f"Also {ranks}: another interview with {name}")
            seen.setdefault(key, []).append(e["rank"])
    return notes


def render(data: dict, top_claims: int = 3, top: int | None = None, show_links: dict | None = None) -> str:
    show_links = show_links or {}
    eps = data["episodes"][:top] if top else data["episodes"]
    shown = f"Top {len(eps)} of {len(data['episodes'])} ranked episodes" if len(eps) < len(data["episodes"]) else f"Episodes: {len(eps)}"
    lines = [
        f"# Podcast alpha · Week of {data['week_of']} ranking (MVP)",
        "",
        f"Ranked by {data['ranked_by']} from `{data['run_dir']}`. {shown}. Claims were checked against the transcripts "
        "(episode ids, timestamps, spoken numbers), not against filings or other sources.",
    ]
    if data.get("notes"):
        lines += ["", data["notes"]]
    lines += ["", "| # | Score | Episode | Source | Published | Why |", "|---|---|---|---|---|---|"]
    for e in eps:
        title = e["title"].replace("|", "/")
        lines.append(f"| {e['rank']} | {e['score']} | {link(title, e['url'])} | {e['source']} (T{e['tier']}) | "
                     f"{e['published'][:10]} | {e['reason'].replace('|', '/')} |")
    lines.append("")
    repeats = repeat_notes(eps)
    for i, e in enumerate(eps):
        lines += [f"## {e['rank']}. {e['title']} · {e['score']}/10", "",
                  f"{e['source']} (T{e['tier']}) · {e['published'][:10]}", "", e["reason"], ""]
        for note in repeats.get(i, []):
            lines += [f"_{note}_", ""]
        if (tu := e.get("transcript_url")):
            lines += [f"Transcript: [{transcript_label(tu)}]({tu})", ""]
        if (sl := show_line(show_links.get(e["source"].lower()))):
            lines += [sl, ""]
        if not e["claims"]:
            lines += ["_No claims._", ""]
            continue
        for c in e["claims"][:top_claims]:
            tick = ", ".join(c["ticker"]) or "-"
            lines.append(f"- `{tick}` {c['claim']} ({c['speaker']}, {ts_link(e['url'], c['timestamp'])}; "
                         f"novelty {c['novelty']}/3, verifiable {c['verifiable']}/3)")
        lines.append("")
    return "\n".join(lines)
