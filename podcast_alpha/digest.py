from __future__ import annotations

from .transcribe import fmt_ts


def ts_link(url: str, seconds: int) -> str:
    if "youtube.com/watch" in url:
        return f"[{fmt_ts(seconds)}]({url}&t={int(seconds)}s)"
    return fmt_ts(seconds)


def render(data: dict, top_claims: int = 3) -> str:
    eps = data["episodes"]
    lines = [
        f"# Podcast alpha · Week of {data['week_of']} ranking (MVP)",
        "",
        f"Ranked by {data['ranked_by']} from `{data['run_dir']}`. Episodes: {len(eps)}. Claims are unverified.",
    ]
    if data.get("notes"):
        lines += ["", data["notes"]]
    lines += ["", "| # | Score | Episode | Source | Published | Why |", "|---|---|---|---|---|---|"]
    for e in eps:
        title = e["title"].replace("|", "/")
        lines.append(f"| {e['rank']} | {e['score']} | [{title}]({e['url']}) | {e['source']} (T{e['tier']}) | "
                     f"{e['published'][:10]} | {e['reason'].replace('|', '/')} |")
    lines.append("")
    for e in eps:
        lines += [f"## {e['rank']}. {e['title']} · {e['score']}/10", "",
                  f"{e['source']} (T{e['tier']}) · {e['published'][:10]}", "", e["reason"], ""]
        if not e["claims"]:
            lines += ["_No claims._", ""]
            continue
        for c in e["claims"][:top_claims]:
            tick = ", ".join(c["ticker"]) or "-"
            lines.append(f"- `{tick}` {c['claim']} ({c['speaker']}, {ts_link(e['url'], c['timestamp'])}; "
                         f"novelty {c['novelty']}/3, verifiable {c['verifiable']}/3)")
        lines.append("")
    return "\n".join(lines)
