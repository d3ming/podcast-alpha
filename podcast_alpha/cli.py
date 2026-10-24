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
    from . import links as links_mod, report

    run_dir = Path(args.ranked).resolve().parent
    if (run_dir / "manifest.json").exists():
        man = {r["episode_id"]: r for r in json.loads((run_dir / "manifest.json").read_text()).get("episodes", [])}
        for e in data["episodes"]:
            if not e.get("transcript_url") and (u := links_mod.transcript_link(man.get(e["episode_id"], {}))):
                e["transcript_url"] = u
    errors, grounding = report.ranked_verify(data, run_dir)
    for w in warnings + grounding:
        print(f"[publish] warning: {w}")
    if errors:
        sys.exit("\n".join(f"[publish] invalid: {e}" for e in errors))
    from . import showlinks

    md = render(data, top_claims=args.top_claims, top=args.top, show_links=showlinks.by_name(ROOT / "show_links.yaml"))
    out = Path(args.out or Path(args.ranked).with_name(f"digest-{data['week_of']}.md"))
    out.write_text(md)
    print(f"[publish] {out}")
    if os.environ.get("NOTION_TOKEN") and (args.notion_parent or os.environ.get("NOTION_PARENT_PAGE_ID")):
        title = f"Podcast alpha · Week of {data['week_of']} ranking (MVP)"
        print(f"[notion] {notion.publish(md, title, args.notion_parent)}")
    else:
        print("[notion] no NOTION_TOKEN; publish the markdown with the Notion connector")


KINDS = ("selected", "report")


def infer_kind(path: str) -> str:
    name = Path(path).name.lower()
    for k in KINDS:
        if name.startswith(k):
            return k
    sys.exit(f"[check] cannot infer schema from {name!r}; pass one of {', '.join(KINDS)} (e.g. check selected {path})")


def check(args) -> None:
    from . import ranked, report

    kind = args.kind
    if args.file is None:  # one positional given: it is the file
        kind, args.file = None, args.kind
    kind = kind or infer_kind(args.file)
    if kind not in KINDS:
        sys.exit(f"[check] unknown schema {kind!r}; use one of {', '.join(KINDS)}")
    try:
        data = report.validate(json.loads(Path(args.file).read_text()), kind)
    except ranked.RankedError as exc:
        sys.exit(f"[check] invalid: {exc}")
    run_dir = Path(args.file).resolve().parent
    warnings = report.warn(kind, data, run_dir)
    if args.captions:
        if kind == "report":
            warnings += report.caption_warn(data, run_dir)
        else:
            warnings.append("--captions only applies to report.json; ignored")
    for w in warnings:
        print(f"[check] warning: {w}")
    if errors := report.verify(kind, data, Path(args.file).resolve().parent):
        sys.exit("\n".join(f"[check] invalid: {e}" for e in errors))
    print(f"[check] {args.file} ok")


def mark(args) -> None:
    from .timing import mark as do_mark

    t = do_mark(Path(args.run), args.stage, args.event)["timing"]
    print(f"[mark] {args.stage} {args.event} {t[args.stage][args.event]}" + (f" ({t[args.stage]['seconds']} s)" if args.event == "end" and "seconds" in t[args.stage] else ""))


def stats(args) -> None:
    from .timing import stats as table

    print(table(Path(args.runs)), end="")


def links(args) -> None:
    from . import ranked, report
    from .links import fill

    sel_p = Path(args.run) / "selected.json"
    if not sel_p.exists():
        sys.exit(f"[links] no selected.json in {args.run}")
    try:
        data = report.validate(json.loads(sel_p.read_text()), "selected")
    except ranked.RankedError as exc:
        sys.exit(f"[links] invalid: {exc}")
    if errors := report.verify("selected", data, Path(args.run)):
        sys.exit("\n".join(f"[links] invalid: {e}" for e in errors))
    for eid, (yt, lu) in fill(Path(args.run), ROOT / "sources.yaml", ytsearch=args.ytsearch,
                                                                   show_links=ROOT / "show_links.yaml").items():
        print(f"[links] {eid} youtube={yt or '-'} listen={lu or '-'}")


def transcript_links_cmd(args) -> None:
    from .links import backfill_transcript_urls

    r = backfill_transcript_urls(Path(args.run), Path(args.sources))
    print(f"[transcript-links] {r['found']} of {r['missing']} publisher transcripts linked in {args.run}")


def show_links(args) -> None:
    from . import showlinks

    path = Path(args.file)
    changed = showlinks.fill(Path(args.sources), path, set(args.only.split(",")) if args.only else None,
                              ytsearch=args.ytsearch)
    data = showlinks.load(path)
    for sid, got in changed.items():
        missing = [f for f in showlinks.FIELDS if not data[sid][f]]
        print(f"[show-links] {sid}: filled {', '.join(got) or '-'}; still blank {', '.join(missing) or '-'}")
    print(f"[show-links] {path} ({len(data)} shows). Edit any value to correct it; filled values are never overwritten.")


def render_report(args) -> None:
    from . import notion, ranked, report

    try:
        data = json.loads(Path(args.report).read_text())
        report.attach_links(data, Path(args.report).parent)
        data = report.validate(data, "report")
    except ranked.RankedError as exc:
        sys.exit(f"[report] invalid: {exc}")
    if errors := report.verify("report", data, Path(args.report).resolve().parent):
        sys.exit("\n".join(f"[report] invalid: {e}" for e in errors))
    for w in report.warn("report", data, Path(args.report).resolve().parent):
        print(f"[report] warning: {w}")
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
    f.add_argument("--youtube-mirror", action="store_true",
                   help="for episodes without a feed transcript, search YouTube for the same episode and use its captions "
                        "(Mac/residential IP only; runs before whisper)")


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
    t.add_argument("--top", type=int, default=8, help="triage: whisper only the top N by metadata score, plus core people (0 = all)")
    t.add_argument("--people", default=str(ROOT / "people.yaml"))
    t.add_argument("--retriage", action="store_true", help="recompute triage even if the manifest already has it")
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
    d.add_argument("--top", type=int, default=8, help="queued transcribe whispers only the top N by triage score, plus core people")
    d.add_argument("--tmtb", help="reference email body to run tmtb-import as a recall check after fetch")
    c = sub.add_parser("check", help="validate selected.json or report.json")
    c.add_argument("kind", help="selected|report, or just the file (schema inferred from selected*.json / report*.json)")
    c.add_argument("file", nargs="?")
    c.add_argument("--captions", action="store_true",
                   help="report only, network: compare cite numbers/timestamps with the linked YouTube video's captions")
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
    lk = sub.add_parser("links", help="resolve youtube_url (verified) and listen_url (Apple/Spotify/episode page) into selected.json")
    lk.add_argument("run")
    lk.add_argument("--ytsearch", action="store_true",
                    help="last resort when no YouTube video was found: yt-dlp ytsearch5 (run from a Mac/residential IP; results still oEmbed-verified)")
    tl = sub.add_parser("transcript-links", help="record the hosted transcript URL for older manifests (re-reads the feeds)")
    tl.add_argument("run")
    tl.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    sl = sub.add_parser("show-links", help="fill blank per-podcast links (site, YouTube, Apple, Spotify) in show_links.yaml")
    sl.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    sl.add_argument("--file", default=str(ROOT / "show_links.yaml"))
    sl.add_argument("--only", help="comma-separated source ids")
    sl.add_argument("--ytsearch", action="store_true", help="also search YouTube (yt-dlp) for blank channels; Mac/residential IP only")
    mk = sub.add_parser("mark", help="record a stage start/end timestamp in the run's manifest.json")
    mk.add_argument("stage", choices=["fetch", "transcribe", "select", "report", "publish"])
    mk.add_argument("event", choices=["start", "end"])
    mk.add_argument("--run", required=True)
    st = sub.add_parser("stats", help="trend table across runs: stage durations, e2e, transcribed, recall")
    st.add_argument("--runs", default=str(ROOT / "runs"))
    p = sub.add_parser("publish", help="validate ranked.json and render the digest")
    p.add_argument("ranked")
    p.add_argument("--out")
    p.add_argument("--top-claims", type=int, default=3)
    p.add_argument("--top", type=int, help="digest only the top N ranked episodes (ranked.json keeps all)")
    p.add_argument("--notion-parent", help="Notion parent page id (API publish needs NOTION_TOKEN)")
    args = ap.parse_args(argv)
    {"fetch": fetch, "daily": daily, "merge": merge, "transcribe": transcribe_run, "publish": publish, "check": check,
     "report": render_report, "tmtb-import": tmtb_import, "links": links, "show-links": show_links, "transcript-links": transcript_links_cmd, "mark": mark, "stats": stats}[args.cmd](args)


if __name__ == "__main__":
    main()
