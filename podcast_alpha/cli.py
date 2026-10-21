import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import holdings as H
from . import llm, notion
from .config import load_sources
from .digest import render, week_of
from .extract import extract
from .ingest import discover
from .score import episode_components, episode_raw, reason, score_claims, score_episodes
from .store import Store
from .transcribe import get_transcript


def run(args) -> Path:
    now = datetime.now(timezone.utc)
    sources, t3_cap = load_sources(args.sources)
    if args.only:
        sources = [s for s in sources if s.id in args.only.split(",")]
    store = Store(args.db)
    p = llm.available_provider()
    print(f"[llm] {'using ' + p[1] + ' (key from ' + p[0] + ')' if p else 'no provider key found; heuristic extractor'}")

    episodes = discover(sources, now, args.days, t3_cap, per_source=args.per_source)
    rows, batch = [], []
    for ep in episodes:
        if store.seen(ep.id) and not args.rescore:
            print(f"[dedupe] skip {ep.id}")
            continue
        segs, tsrc = get_transcript(ep)
        claims, extractor = ([], "none")
        if segs:
            claims, extractor = extract(segs, ep.title, store.recent_claims(exclude_episode=ep.id))
        minutes = (segs[-1]["start"] / 60) if segs else 0
        print(f"[episode] {ep.source_id}: transcript={tsrc or 'none'} claims={len(claims)}")
        rows.append({"ep": ep, "claims": claims, "segs": bool(segs), "minutes": minutes,
                     "transcript_source": tsrc, "extractor": extractor})
        batch += [(c, ep.tier) for c in claims]
    score_claims(batch)

    weights = {} if args.no_holdings else H.fetch_weights()
    out = []
    for r in rows:
        ep, claims = r["ep"], sorted(r["claims"], key=lambda c: c["score"], reverse=True)
        comp = episode_components(ep, claims, r["minutes"]) if r["segs"] else {
            "tier": {1: 10.0, 2: 6.0, 3: 3.0}[ep.tier], "novelty": 0, "verifiability": 0,
            "specificity": 0, "speaker_access": 0}
        raw = episode_raw(comp)
        if weights:
            raw = H.relevance(raw, [t for c in claims for t in c["ticker"]], weights)
        out.append({"title": ep.title, "url": ep.url, "source": ep.source_name, "tier": ep.tier,
                    "published": ep.published_at.isoformat(), "youtube_id": ep.youtube_id,
                    "transcript_source": r["transcript_source"], "claims": claims, "raw": raw,
                    "reason": reason(comp, len(claims), r["segs"]), "id": ep.id})
    score_episodes(out)
    out.sort(key=lambda e: (e["score"], e["raw"]), reverse=True)

    meta = {"week_of": week_of(now, args.days), "days": args.days, "run_at": now.strftime("%Y-%m-%d %H:%M UTC"),
            "extractor": f"LLM ({p[1]})" if p else "heuristic (no LLM key set)",
            "holdings": "off" if args.no_holdings or not weights else "on (weights used for ordering only, not shown)"}
    md = render(out, meta, top_claims=args.top_claims)
    path = Path(args.out) / f"digest-{meta['week_of']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md)
    if args.json:
        safe = [{k: v for k, v in e.items() if k != "raw"} for e in out]
        path.with_suffix(".json").write_text(json.dumps(safe, indent=2))
    for r in rows:
        store.add_episode(r["ep"], r["transcript_source"])
        store.add_claims(r["ep"].id, r["claims"])
    print(f"[digest] {path}")
    if args.notion:
        title = f"Podcast alpha · Week of {meta['week_of']} ranking (MVP)"
        print(f"[notion] {notion.publish(md, title, args.notion_parent)}")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="podcast-alpha")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ingest, transcribe, extract, score, write the digest")
    r.add_argument("--days", type=int, default=7)
    r.add_argument("--sources", default="sources.yaml")
    r.add_argument("--db", default="data/podcast_alpha.sqlite")
    r.add_argument("--out", default="out")
    r.add_argument("--only", help="comma-separated source ids")
    r.add_argument("--per-source", type=int, default=3)
    r.add_argument("--top-claims", type=int, default=3)
    r.add_argument("--no-holdings", action="store_true", help="skip holdings weighting")
    r.add_argument("--rescore", action="store_true", help="re-process episodes already in the DB")
    r.add_argument("--json", action="store_true", help="also write a JSON copy of the digest")
    r.add_argument("--notion", action="store_true", help="publish the digest to Notion")
    r.add_argument("--notion-parent", help="Notion parent page id")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args)


if __name__ == "__main__":
    main()
