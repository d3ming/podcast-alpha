"""Build the static website from published/<run>/ (standard library only; no network, no LLM).

    python3 -m podcast_alpha.site --out dist
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
GITHUB = "https://github.com/d3ming/podcast-alpha"
TIERS = {1: "Tier 1", 2: "Tier 2", 3: "Tier 3"}

CSS = """\
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--line:#e4e1d9;--card:#ffffff;--accent:#0b5c4d;--accent-bg:#e3f1ed;
--chip:#f0eee8;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#141516;--fg:#e8e6e1;--muted:#9a9a9f;--line:#2b2c2e;--card:#1b1c1e;
--accent:#6fcfb8;--accent-bg:#17302b;--chip:#25262a;--warn:#e0b25a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:0 20px}
header.site{border-bottom:1px solid var(--line);padding:18px 0}
header.site .wrap{display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline;justify-content:space-between}
.brand{font-weight:700;letter-spacing:-.01em;color:var(--fg);text-decoration:none}
nav a{margin-right:18px;color:var(--muted);text-decoration:none;font-size:15px}
nav a:last-child{margin-right:0}
nav a:hover,nav a[aria-current]{color:var(--accent)}
main{padding:36px 0 64px}
h1{font-size:2rem;line-height:1.2;letter-spacing:-.02em;margin:0 0 .4em}
h2{font-size:1.25rem;margin:2.2em 0 .6em;letter-spacing:-.01em}
h3{font-size:1.1rem;line-height:1.35;margin:0 0 .3em}
p{margin:.7em 0}
a{color:var(--accent);text-underline-offset:3px}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
.lede{font-size:1.15rem;color:var(--muted);margin:0 0 1.5em}
.meta{color:var(--muted);font-size:14px;margin:.2em 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}
.card .top{display:flex;gap:12px;align-items:flex-start}
.badge{flex:none;min-width:2.4rem;text-align:center;border-radius:8px;background:var(--accent-bg);color:var(--accent);
font-weight:700;padding:2px 8px;font-variant-numeric:tabular-nums}
.rank{color:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}
.reason{margin:.6em 0}
ul.claims{list-style:none;padding:0;margin:.8em 0 0}
ul.claims li{padding:.7em 0;border-top:1px solid var(--line)}
ul.claims li:first-child{border-top:0}
.who{font-weight:600;font-size:15px}
.chip{display:inline-block;font-size:13px;line-height:1.5;padding:0 8px;border-radius:999px;background:var(--chip);
color:var(--fg);text-decoration:none;margin:0 4px 2px 0;font-variant-numeric:tabular-nums;border:1px solid transparent}
a.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip.ts{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.chip.tk{font-weight:600;background:var(--accent-bg);color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.runs{list-style:none;padding:0}
.runs li{padding:10px 0;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:4px 12px;justify-content:space-between}
.tickers{display:flex;flex-wrap:wrap;gap:6px;padding:0;list-style:none}
footer{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:14px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em;background:var(--chip);padding:1px 5px;border-radius:4px}
pre{background:var(--chip);padding:12px 14px;border-radius:8px;overflow-x:auto;font-size:14px}
.notice{border-left:3px solid var(--warn);padding:2px 14px;color:var(--muted)}
@media (max-width:480px){body{font-size:16px}h1{font-size:1.65rem}.wrap{padding:0 16px}.card{padding:14px 14px}}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def safe_url(u) -> str:
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else ""


def ext_link(u, text: str, cls: str = "") -> str:
    u = safe_url(u)
    if not u:
        return ""
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{esc(u)}" rel="noopener noreferrer">{esc(text)}</a>'


def fmt_ts(sec) -> str:
    if not isinstance(sec, int) or sec < 0:
        return ""
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def slug(sym: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", sym)


def is_private(t: str) -> bool:
    return t.startswith("private:")


def page(title: str, body: str, depth_nav: str = "", desc: str = "") -> str:
    nav = [("/", "Latest", "latest"), ("/tickers/", "Tickers", "tickers"), ("/about.html", "About", "about")]
    links = "".join(f'<a href="{h}"{" aria-current=\"page\"" if k == depth_nav else ""}>{n}</a>' for h, n, k in nav)
    d = f'<meta name="description" content="{esc(desc)}">' if desc else ""
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>{d}<link rel="stylesheet" href="/style.css"></head><body>'
            f'<header class="site"><div class="wrap"><a class="brand" href="/">podcast-alpha</a><nav aria-label="Main">{links}</nav></div></header>'
            f'<main><div class="wrap">{body}</div></main>'
            f'<footer><div class="wrap">Claims come from podcast transcripts and are not investment advice. '
            f'<a href="/about.html">About</a> · <a href="/runs.json">runs.json</a> · <a href="/claims.jsonl">claims.jsonl</a> · '
            f'<a href="{GITHUB}">GitHub</a></div></footer></body></html>\n')


# ---------------------------------------------------------------- loading

def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(p: Path) -> list[dict]:
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        pass
    return rows


def load_run(d: Path) -> dict | None:
    name = d.name
    ranked = read_json(d / "ranked.json")
    report = read_json(d / "report.json")
    if not isinstance(ranked, dict):
        ranked = None
    if not isinstance(report, dict):
        report = None
    claims = read_jsonl(d / "claims.jsonl")
    meta = {m.get("episode_id"): m for m in (read_json(d / "episodes.json", []) or []) if isinstance(m, dict)}
    if not (ranked or report or claims):
        return None
    kind = "weekly" if ranked else "daily"
    date = (ranked or {}).get("week_of") or (report or {}).get("date") or (re.match(r"\d{4}-\d{2}-\d{2}", name) or [""])[0]
    by_ep: dict[str, list[dict]] = {}
    for c in claims:
        by_ep.setdefault(c.get("episode_id", ""), []).append(c)
    eps = []
    if ranked:
        for i, e in enumerate(ranked.get("episodes") or [], 1):
            eid = e.get("episode_id", "")
            cl = by_ep.get(eid) or [{"speaker": c.get("speaker"), "timestamp": c.get("timestamp"), "text": c.get("claim"),
                                      "tickers": c.get("ticker") or [], "novelty": c.get("novelty"),
                                      "verifiable": c.get("verifiable"), "link": ""} for c in e.get("claims") or []]
            m = meta.get(eid, {})
            eps.append({"id": eid, "rank": e.get("rank") or i, "score": e.get("score"), "title": e.get("title"),
                        "show": e.get("source") or m.get("source"), "tier": e.get("tier"), "published": e.get("published"),
                        "reason": e.get("reason"), "url": e.get("url"), "claims": cl,
                        "transcript_url": (by_ep.get(eid) or [{}])[0].get("transcript_url") or e.get("transcript_url")})
    else:
        titles = {e.get("episode_id"): e for e in (report or {}).get("essential") or []}
        order = [e.get("episode_id") for e in (report or {}).get("episodes") or []] or list(by_ep)
        for i, eid in enumerate(order, 1):
            cl = by_ep.get(eid, [])
            first = cl[0] if cl else {}
            m = meta.get(eid, {})
            rep = next((e for e in (report or {}).get("episodes") or [] if e.get("episode_id") == eid), {})
            eps.append({"id": eid, "rank": i, "score": first.get("score"),
                        "title": first.get("title") or m.get("title") or rep.get("headline"),
                        "show": first.get("show") or m.get("show") or m.get("source") or titles.get(eid, {}).get("show"),
                        "tier": first.get("tier") or m.get("tier"), "published": first.get("published") or m.get("published"),
                        "reason": rep.get("headline") if first.get("title") else None,
                        "url": first.get("url") or rep.get("url") or m.get("url"), "claims": cl,
                        "transcript_url": first.get("transcript_url") or rep.get("transcript_url")})
    fetched = len(meta) or None
    notes = (ranked or {}).get("notes") or ""
    return {"name": name, "kind": kind, "date": date, "episodes": eps, "claims": claims, "fetched": fetched,
            "transcribed": sum(1 for m in meta.values() if m.get("has_transcript")) if meta else None,
            "notes": notes, "insights": (report or {}).get("key_insights") or [], "title": (report or {}).get("title"),
            "dir": d}


def load_runs(published: Path) -> list[dict]:
    runs = [r for d in sorted(published.iterdir()) if d.is_dir() and (r := load_run(d))] if published.is_dir() else []
    runs.sort(key=lambda r: (r["date"], r["name"]), reverse=True)
    return runs


# ---------------------------------------------------------------- rendering

def ticker_chip(t: str) -> str:
    if is_private(t):
        return f'<span class="chip" title="Private company">{esc(t[8:])}</span>'
    return f'<a class="chip tk" href="/tickers/{esc(quote(slug(t)))}.html">{esc(t)}</a>'


def claim_li(c: dict, show_ep: bool = False) -> str:
    ts = fmt_ts(c.get("timestamp"))
    link = safe_url(c.get("link")) or safe_url(c.get("url"))
    chip = ""
    if ts:
        chip = (f'<a class="chip ts" href="{esc(link)}" rel="noopener noreferrer" aria-label="Open at {esc(ts)}">{esc(ts)}</a>'
                if link else f'<span class="chip ts">{esc(ts)}</span>')
    tk = "".join(ticker_chip(t) for t in c.get("tickers") or [] if isinstance(t, str))
    sub = []
    if c.get("novelty") is not None:
        sub.append(f"novelty {esc(c['novelty'])}/3")
    if c.get("verifiable") is not None:
        sub.append(f"verifiable {esc(c['verifiable'])}/3")
    ep = ""
    if show_ep:
        ep = (f'<div class="meta">{esc(c.get("show"))} · {esc(c.get("title"))} · {esc(c.get("date") or c.get("published"))} · '
              f'<a href="/runs/{esc(quote(str(c.get("run", ""))))}/">{esc(c.get("run"))}</a></div>')
    return (f'<li><div class="who">{esc(c.get("speaker") or "unknown")} {chip}</div><div>{esc(c.get("text"))}</div>'
            f'<div>{tk}<span class="sub"> {" · ".join(sub)}</span></div>{ep}</li>')


def episode_card(e: dict, run: str, n_claims: int | None = None) -> str:
    claims = e["claims"] if n_claims is None else e["claims"][:n_claims]
    meta = " · ".join(esc(x) for x in [e.get("show"), TIERS.get(e.get("tier")), e.get("published")] if x)
    score = f'<span class="badge" title="Score out of 10">{esc(e["score"])}</span>' if e.get("score") is not None else ""
    title = esc(e.get("title") or e.get("id"))
    t = ext_link(e.get("url"), e.get("title") or e.get("id") or "") or title
    tr = ext_link(e.get("transcript_url"), "Transcript", "chip")
    reason = f'<p class="reason">{esc(e["reason"])}</p>' if e.get("reason") else ""
    cl = f'<ul class="claims">{"".join(claim_li(c) for c in claims)}</ul>' if claims else ""
    return (f'<article class="card"><div class="top">{score}<div><div class="rank">#{esc(e.get("rank"))}</div>'
            f'<h3>{t}</h3><div class="meta">{meta} {tr}</div></div></div>{reason}{cl}</article>')


def run_page(r: dict) -> str:
    n = len(r["episodes"])
    bits = [f"{n} episodes {'ranked' if r['kind'] == 'weekly' else 'selected'}"]
    if r["fetched"]:
        bits.insert(0, f"{r['fetched']} fetched")
    if r["transcribed"] is not None and r["fetched"]:
        bits.append(f"{r['transcribed']} with transcripts")
    bits.append(f"{len(r['claims'])} claims")
    head = f'<h1>{esc(r["title"] or ("Weekly digest" if r["kind"] == "weekly" else "Daily report"))}</h1>'
    ins = ""
    if r["insights"]:
        ins = "<h2>Key insights</h2><ul>" + "".join(f"<li>{esc(i)}</li>" for i in r["insights"]) + "</ul>"
    notes = f'<p class="notice">{esc(r["notes"])}</p>' if r["notes"] else ""
    feed = f'<p class="meta">Agents: <a href="/runs/{esc(quote(r["name"]))}/claims.jsonl">claims.jsonl</a></p>'
    cards = "".join(episode_card(e, r["name"]) for e in r["episodes"])
    return page(f'{r["name"]} · podcast-alpha',
                f'{head}<p class="meta">{esc(r["kind"].title())} · {esc(r["date"])} · {" · ".join(bits)}</p>{notes}{feed}{ins}{cards}',
                desc=f"{r['kind']} podcast intel for {r['date']}")


def run_list(runs: list[dict]) -> str:
    items = "".join(
        f'<li><a href="/runs/{esc(quote(r["name"]))}/">{esc(r["name"])}</a>'
        f'<span class="meta">{esc(r["kind"])} · {esc(r["date"])} · {len(r["episodes"])} episodes · {len(r["claims"])} claims</span></li>'
        for r in runs)
    return f'<ul class="runs">{items}</ul>'


def index_page(runs: list[dict]) -> str:
    intro = ('<h1>Podcast intel for investors</h1><p class="lede">Every day and week, an agent reads the podcasts that move '
             'markets and keeps what is new: who said what, where, with a link to the exact moment. '
             'Each claim is checked against the transcript.</p>')
    if not runs:
        return page("podcast-alpha", intro + "<p>No runs published yet.</p>", "latest")
    r = runs[0]
    top = "".join(episode_card(e, r["name"], 3) for e in r["episodes"][:5])
    body = (f'{intro}<h2>Latest: <a href="/runs/{esc(quote(r["name"]))}/">{esc(r["name"])}</a></h2>'
            f'<p class="meta">{esc(r["kind"].title())} · {esc(r["date"])} · {len(r["episodes"])} episodes</p>{top}'
            f'<p><a href="/runs/{esc(quote(r["name"]))}/">All {len(r["episodes"])} episodes in this run</a></p>'
            f'<h2>All runs</h2>{run_list(runs)}')
    return page("podcast-alpha", body, "latest", "Claims from investor podcasts, scored and linked to the moment.")


def collect_tickers(runs: list[dict]) -> tuple[dict, dict]:
    pub: dict[str, list[dict]] = {}
    priv: dict[str, list[dict]] = {}
    for r in runs:
        for c in r["claims"]:
            for t in set(x for x in c.get("tickers") or [] if isinstance(x, str) and x):
                (priv if is_private(t) else pub).setdefault(t[8:] if is_private(t) else t, []).append(c)
    return pub, priv


def tickers_index(pub: dict, priv: dict) -> str:
    chips = "".join(
        f'<li><a class="chip tk" href="/tickers/{esc(quote(slug(t)))}.html">{esc(t)}</a><span class="sub">{len(v)}</span></li>'
        for t, v in sorted(pub.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    p = ""
    if priv:
        names = ", ".join(f"{esc(n)} ({len(v)})" for n, v in sorted(priv.items(), key=lambda kv: (-len(kv[1]), kv[0])))
        p = f'<h2>Private companies</h2><p class="meta">{names}</p>'
    return page("Tickers · podcast-alpha", f'<h1>Tickers</h1><p class="meta">Claims mentioning each listed company, across runs.</p>'
                f'<ul class="tickers">{chips}</ul>{p}', "tickers")


def ticker_page(sym: str, claims: list[dict]) -> str:
    claims = sorted(claims, key=lambda c: (str(c.get("date") or c.get("published") or ""), c.get("score") or 0), reverse=True)
    li = "".join(claim_li(c, show_ep=True) for c in claims)
    return page(f"{sym} · podcast-alpha", f'<h1>{esc(sym)}</h1><p class="meta">{len(claims)} claims</p><ul class="claims">{li}</ul>', "tickers")


def about_page(runs: list[dict]) -> str:
    body = f"""<h1>About</h1>
<p class="lede">Code fetches and transcribes podcasts; an agent picks what matters and writes what was said. Code then checks the agent's work before anything is published.</p>
<h2>How it works</h2>
<p>Deterministic code reads feeds from dozens of investing, tech and earnings shows, gets each transcript from the cheapest good source (the publisher, YouTube captions, or local Whisper), and stores the result. An agent scores episodes for new, checkable executive or investor signal and writes claims, each with a speaker and a timestamp. Code validates the output against a schema and grounds every claim in the transcript: the episode exists, the timestamp falls inside it, numbers are spoken near that time, and no long passage is copied.</p>
<p>Daily runs pick three episodes. Weekly runs rank about forty and publish the top fifteen. The source and the full method are on <a href="{GITHUB}">GitHub</a>.</p>
<h2>Caveats</h2>
<p class="notice">Claims are checked against transcripts, not against company filings. Speakers may be talking their book. Whisper transcripts can misspell names. Nothing here is investment advice.</p>
<h2>Feeds for agents</h2>
<ul>
<li><a href="/runs.json">/runs.json</a>: index of runs with date, kind, counts and paths.</li>
<li><a href="/claims.jsonl">/claims.jsonl</a>: every claim from every run, newest first, one JSON object per line.</li>
<li><code>/runs/&lt;run&gt;/claims.jsonl</code>: one run's claims. Each claim has <code>speaker</code>, <code>timestamp</code>, <code>tickers</code> and a <code>link</code> that opens the source at that moment.</li>
</ul>
<p class="meta">{len(runs)} runs published.</p>"""
    return page("About · podcast-alpha", body, "about")


# ---------------------------------------------------------------- build

def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def build(published: Path, out: Path) -> dict:
    runs = load_runs(published)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    write(out / "style.css", CSS)
    write(out / "index.html", index_page(runs))
    write(out / "about.html", about_page(runs))
    all_claims, index = [], []
    for r in runs:
        write(out / "runs" / r["name"] / "index.html", run_page(r))
        src = r["dir"] / "claims.jsonl"
        if src.exists():
            shutil.copyfile(src, out / "runs" / r["name"] / "claims.jsonl")
        all_claims += r["claims"]
        index.append({"run": r["name"], "kind": r["kind"], "date": r["date"], "episodes": len(r["episodes"]),
                      "claims": len(r["claims"]), "page": f"/runs/{r['name']}/",
                      "claims_path": f"/runs/{r['name']}/claims.jsonl"})
    write(out / "claims.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in all_claims))
    write(out / "runs.json", json.dumps({"runs": index, "claims_path": "/claims.jsonl"}, indent=1, ensure_ascii=False) + "\n")
    pub, priv = collect_tickers(runs)
    write(out / "tickers" / "index.html", tickers_index(pub, priv))
    for sym, cl in pub.items():
        write(out / "tickers" / f"{slug(sym)}.html", ticker_page(sym, cl))
    return {"runs": len(runs), "tickers": len(pub), "claims": len(all_claims)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="podcast-alpha site", description="build the static website from published/")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--published", default=str(ROOT / "published"))
    args = ap.parse_args(argv)
    s = build(Path(args.published), Path(args.out))
    print(f"[site] built {s['runs']} runs, {s['tickers']} tickers, {s['claims']} claims into {args.out}")


if __name__ == "__main__":
    main()
