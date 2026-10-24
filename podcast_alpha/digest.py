from __future__ import annotations

from .links import transcript_label
from .transcribe import fmt_ts


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
    for e in eps:
        lines += [f"## {e['rank']}. {e['title']} · {e['score']}/10", "",
                  f"{e['source']} (T{e['tier']}) · {e['published'][:10]}", "", e["reason"], ""]
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
