from datetime import datetime

from .transcribe import fmt_ts


def ts_link(ep: dict, seconds: int) -> str:
    url = ep["url"]
    if ep.get("youtube_id"):
        url = f"https://www.youtube.com/watch?v={ep['youtube_id']}&t={int(seconds)}s"
    return f"[{fmt_ts(seconds)}]({url})"


def render(episodes: list[dict], meta: dict, top_claims: int = 3) -> str:
    lines = [
        f"# Podcast alpha · Week of {meta['week_of']} ranking (MVP)",
        "",
        f"Window: last {meta['days']} days to {meta['run_at']}. Episodes: {len(episodes)}. "
        f"Extraction: {meta['extractor']}. Holdings weighting: {meta['holdings']}.",
        "Scores are 1-10 with a forced spread across this batch (top 10% at 8+, median near 5, 30% at 4 or less). "
        "Claims are unverified.",
        "",
        "| # | Score | Episode | Source | Published | Why |",
        "|---|---|---|---|---|---|",
    ]
    for i, ep in enumerate(episodes, 1):
        title = ep["title"].replace("|", "/")
        lines.append(f"| {i} | {ep['score']} | [{title}]({ep['url']}) | {ep['source']} (T{ep['tier']}) | "
                     f"{ep['published'][:10]} | {ep['reason']} |")
    lines.append("")
    for i, ep in enumerate(episodes, 1):
        lines += [f"## {i}. {ep['title']} · {ep['score']}/10", "",
                  f"{ep['source']} · {ep['published'][:10]} · transcript: {ep['transcript_source'] or 'none'}", "",
                  ep["reason"], ""]
        claims = ep["claims"][:top_claims]
        if not claims:
            lines += ["_No claims extracted._", ""]
            continue
        for c in claims:
            tick = ", ".join(c["ticker"]) or "-"
            lines.append(f"- **{c['score']}/10** `{tick}` {c['claim']} ({c['speaker']}, {ts_link(ep, c['timestamp'])}; "
                         f"novelty {c['novelty']}/3, verifiable {c['verifiable']}/3)")
        lines.append("")
    return "\n".join(lines)


def week_of(now: datetime, days: int) -> str:
    from datetime import timedelta
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")
