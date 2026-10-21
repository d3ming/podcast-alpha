from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .fetch import fetch, merge

ROOT = Path(__file__).resolve().parent.parent


def publish(args) -> None:
    from . import notion, ranked
    from .digest import render

    try:
        data = ranked.load(args.ranked)
        warnings = ranked.validate(data)
    except ranked.RankedError as exc:
        sys.exit(f"[publish] invalid: {exc}")
    for w in warnings:
        print(f"[publish] warning: {w}")
    md = render(data, top_claims=args.top_claims)
    out = Path(args.out or Path(args.ranked).with_name(f"digest-{data['week_of']}.md"))
    out.write_text(md)
    print(f"[publish] {out}")
    if os.environ.get("NOTION_TOKEN") and (args.notion_parent or os.environ.get("NOTION_PARENT_PAGE_ID")):
        title = f"Podcast alpha · Week of {data['week_of']} ranking (MVP)"
        print(f"[notion] {notion.publish(md, title, args.notion_parent)}")
    else:
        print("[notion] no NOTION_TOKEN; publish the markdown with the Notion connector")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="podcast-alpha")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="ingest episodes and write transcripts plus manifest.json to a run folder")
    f.add_argument("--days", type=int, default=7)
    f.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    f.add_argument("--db", default=str(ROOT / "data" / "podcast_alpha.sqlite"))
    f.add_argument("--runs", default=str(ROOT / "runs"))
    f.add_argument("--only", help="comma-separated source ids")
    f.add_argument("--per-source", type=int, default=3)
    f.add_argument("--people", default=str(ROOT / "people.yaml"), help="people watchlist; empty string disables")
    f.add_argument("--per-person", type=int, default=3)
    f.add_argument("--run-name", help="run folder name (default: today's date)")
    f.add_argument("--youtube-only", action="store_true", help="YouTube channels and person search only, written to --out")
    f.add_argument("--out", default="pa-yt-out", help="output dir for --youtube-only")
    m = sub.add_parser("merge", help="merge a --youtube-only output dir into a run folder")
    m.add_argument("dir")
    m.add_argument("--into", required=True)
    m.add_argument("--db", default=str(ROOT / "data" / "podcast_alpha.sqlite"))
    f.add_argument("--refetch", action="store_true", help="include episodes already in the dedupe DB")
    p = sub.add_parser("publish", help="validate ranked.json and render the digest")
    p.add_argument("ranked")
    p.add_argument("--out")
    p.add_argument("--top-claims", type=int, default=3)
    p.add_argument("--notion-parent", help="Notion parent page id (API publish needs NOTION_TOKEN)")
    args = ap.parse_args(argv)
    {"fetch": fetch, "merge": merge, "publish": publish}[args.cmd](args)


if __name__ == "__main__":
    main()
