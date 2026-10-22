from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .fetch import daily, fetch, merge, transcribe_run

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


def check(args) -> None:
    from . import ranked, report

    try:
        report.validate(json.loads(Path(args.file).read_text()), args.kind)
    except ranked.RankedError as exc:
        sys.exit(f"[check] invalid: {exc}")
    print(f"[check] {args.file} ok")


def render_report(args) -> None:
    from . import notion, ranked, report

    try:
        data = report.validate(json.loads(Path(args.report).read_text()), "report")
    except ranked.RankedError as exc:
        sys.exit(f"[report] invalid: {exc}")
    out = Path(args.out or Path(args.report).with_name("report.md"))
    out.write_text(report.render(data))
    print(f"[report] {out}")
    if not os.environ.get("NOTION_TOKEN"):
        print(f"[notion] NOTION_TOKEN not set; skipped the API. Publish {out} through a Notion connector.")
    elif not (args.notion_parent or notion.parent()):
        print(f"[notion] no parent page (NOTION_PARENT_PAGE_ID, publish.yaml or --notion-parent); publish {out} through a Notion connector.")
    else:
        print(f"[notion] {notion.publish(out.read_text(), data['title'], args.notion_parent)}")


def tmtb_import(args) -> None:
    from .tmtb import run

    text = sys.stdin.read() if args.email == "-" else Path(args.email).read_text()
    run_dir = Path(args.run) if args.run else ROOT / "runs" / run_date(args.date)
    if not (run_dir / "manifest.json").exists():
        sys.exit(f"[tmtb] no manifest in {run_dir}; pass --run or --date")
    run(text, run_dir, days=args.days)


def run_date(value: str, today=None) -> str:
    from datetime import date, timedelta

    today = today or date.today()
    if value == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if value == "today":
        return today.isoformat()
    return date.fromisoformat(value).isoformat()


def add_fetch_args(f) -> None:
    f.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    f.add_argument("--db", default=str(ROOT / "data" / "podcast_alpha.sqlite"))
    f.add_argument("--runs", default=str(ROOT / "runs"))
    f.add_argument("--only", help="comma-separated source ids")
    f.add_argument("--per-source", type=int, default=3)
    f.add_argument("--people", default=str(ROOT / "people.yaml"), help="people watchlist; empty string disables")
    f.add_argument("--per-person", type=int, default=3)
    f.add_argument("--run-name", help="run folder name (default: today's date)")
    f.add_argument("--whisper-minutes", type=float, help="whisper minutes inside fetch (default 0 on the box, 600 with --youtube-only)")
    f.add_argument("--whisper-per-episode", type=float, help="cap per episode in minutes (default: whole episode)")
    f.add_argument("--refetch", action="store_true", help="include episodes already in the dedupe DB")
    f.add_argument("--min-transcript-chars", type=int, default=5000, help="shorter text is flagged fallback, not a transcript")
    f.add_argument("--include-reruns", action="store_true", help="keep replays and re-airs")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="podcast-alpha")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="ingest episodes and write transcripts plus manifest.json to a run folder")
    f.add_argument("--days", type=int, default=7)
    add_fetch_args(f)
    f.add_argument("--youtube-only", action="store_true", help="YouTube channels and person search only, written to --out")
    f.add_argument("--out", default="pa-yt-out", help="output dir for --youtube-only")
    t = sub.add_parser("transcribe", help="whisper untranscribed episodes in a run folder; resumable, run it detached")
    t.add_argument("run")
    t.add_argument("--minutes", type=float, default=600)
    t.add_argument("--per-episode", type=float)
    t.add_argument("--db", help="also record transcript_source in this SQLite DB")
    t.add_argument("--force", action="store_true", help="ignore a stale transcribe.lock")
    t.add_argument("--retry-errors", action="store_true", help="retry episodes that failed before")
    t.add_argument("--include-reruns", action="store_true")
    t.add_argument("--min-transcript-chars", type=int, default=5000)
    t.add_argument("--skip-news", action="store_true", help="skip news/market-recap shows (default: whisper them last)")
    m = sub.add_parser("merge", help="merge a --youtube-only output dir into a run folder")
    m.add_argument("dir")
    m.add_argument("--into", required=True)
    m.add_argument("--db", default=str(ROOT / "data" / "podcast_alpha.sqlite"))
    d = sub.add_parser("daily", help="people first, then channels, for the last --hours; queues transcribe in the background")
    add_fetch_args(d)
    d.add_argument("--hours", type=float, default=48)
    d.add_argument("--minutes", type=float, default=600, help="whisper budget for the queued transcribe")
    d.add_argument("--no-transcribe", action="store_true")
    d.add_argument("--skip-news", action="store_true", help="queued transcribe skips news shows (default: last)")
    d.add_argument("--tmtb", help="reference email body to run tmtb-import as a recall check after fetch")
    c = sub.add_parser("check", help="validate selected.json or report.json")
    c.add_argument("kind", choices=["selected", "report"])
    c.add_argument("file")
    r = sub.add_parser("report", help="validate report.json, render report.md, publish to Notion if configured")
    r.add_argument("report")
    r.add_argument("--out")
    r.add_argument("--notion-parent")
    ti = sub.add_parser("tmtb-import", help="parse a pasted reference email into seeds and list what the run missed")
    ti.add_argument("email", help="path to the email body, or - for stdin")
    ti.add_argument("--run", help="run folder (default: runs/<--date>)")
    ti.add_argument("--date", default="yesterday",
                    help="run date to check: yesterday (default; TMTB lands ~2:40 PM PT, check next morning), today, or YYYY-MM-DD")
    ti.add_argument("--days", type=float, default=4)
    p = sub.add_parser("publish", help="validate ranked.json and render the digest")
    p.add_argument("ranked")
    p.add_argument("--out")
    p.add_argument("--top-claims", type=int, default=3)
    p.add_argument("--notion-parent", help="Notion parent page id (API publish needs NOTION_TOKEN)")
    args = ap.parse_args(argv)
    {"fetch": fetch, "daily": daily, "merge": merge, "transcribe": transcribe_run, "publish": publish, "check": check,
     "report": render_report, "tmtb-import": tmtb_import}[args.cmd](args)


if __name__ == "__main__":
    main()
