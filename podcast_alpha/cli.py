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
    man = {}
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

    shows = showlinks.by_name(ROOT / "show_links.yaml")
    for rank, old_url, new_url in links_mod.episode_urls(data["episodes"][:args.top] if args.top else data["episodes"], shows, man):
        print(f"[publish] #{rank} link: {old_url or '(none)'} -> {new_url}")
    md = render(data, top_claims=args.top_claims, top=args.top, show_links=shows)
    out = Path(args.out or Path(args.ranked).with_name(f"digest-{data['week_of']}.md"))
    out.write_text(md)
    print(f"[publish] {out}")
    after_stage(run_dir, ingest=True)
    if os.environ.get("NOTION_TOKEN") and (args.notion_parent or notion.parent()):
        title = f"Podcast alpha · Week of {data['week_of']} ranking (MVP)"
        print(f"[notion] {notion.publish(md, title, args.notion_parent)}")
    else:
        print("[notion] no NOTION_TOKEN; publish the markdown with the Notion connector")


def after_stage(run_dir: Path, ingest: bool = False) -> None:
    """Bookkeeping code can do for the agent: stamp stage timings, and load a finished run into the library.
    Never fails the command that called it."""
    from . import status

    try:
        if done := status.record_boundaries(run_dir):
            print(f"[timing] recorded {', '.join(done)}")
    except Exception as exc:
        print(f"[timing] warning: could not record stage times: {type(exc).__name__}: {exc}")
    if ingest and not os.environ.get("PA_NO_LIBRARY"):
        from . import library

        try:
            r = library.ingest_run(run_dir)
            print(f"[library] {run_dir.name}: {r.get('claims', 0)} claims, {r.get('episodes', 0)} episodes")
        except Exception as exc:
            print(f"[library] warning: not ingested ({type(exc).__name__}: {exc}); run podcast-alpha library ingest {run_dir}")
    if ingest and not os.environ.get("PA_NO_PUBLISHED"):
        from . import published

        try:
            dest, files = published.export(run_dir)
            rel = dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest
            print(f"[published] {rel}: {', '.join(files)}. Commit it: git add {rel} && git commit -m 'publish {dest.name}' && git push")
        except Exception as exc:
            print(f"[published] warning: not exported ({type(exc).__name__}: {exc}); run podcast-alpha export-published {run_dir}")


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
    for w in report.dedupe_warnings(warnings):
        print(f"[check] warning: {w}")
    if errors := report.verify(kind, data, Path(args.file).resolve().parent):
        sys.exit("\n".join(f"[check] invalid: {e}" for e in errors))
    print(f"[check] {args.file} ok")
    if kind == "selected":
        after_stage(run_dir)


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


def export_published(args) -> None:
    from . import published

    try:
        dest, files = published.export(Path(args.run), name=args.name)
    except FileNotFoundError as exc:
        sys.exit(f"[published] {exc}")
    print(f"[published] {dest}: {', '.join(files)}")


def rank_prep(args) -> None:
    from .rank import prep

    r = prep(Path(args.run), args.batches)
    print(f"[rank-prep] {r['transcribed']} transcribed episodes in {r['batches']} batches under {args.run}/rank "
          f"({r['without_transcript']} without a transcript are not ranked). Give each batch-N-in.json to a reader "
          f"(prompts/rank.md); save its output as rank/batch-N-out.json, then run rank-merge.")


def rank_merge(args) -> None:
    from . import ranked
    from .rank import merge

    run = Path(args.run)
    try:
        data = merge(run, args.ranked_by, args.week_of)
        warnings = ranked.validate(data)
    except (ValueError, ranked.RankedError, FileNotFoundError) as exc:
        sys.exit(f"[rank-merge] {exc}")
    for w in warnings:
        print(f"[rank-merge] warning: {w}")
    out = run / "ranked.json"
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"[rank-merge] {out} ({len(data['episodes'])} episodes). Next: podcast-alpha publish {out} --top 15")


def show_links(args) -> None:
    from . import showlinks

    path = Path(args.file)
    if args.fix and not args.verify:
        sys.exit("[show-links] --fix needs --verify")
    if args.verify:
        results = showlinks.verify(Path(args.sources), path, set(args.only.split(",")) if args.only else None, pause=1.0)
        print(f"{'show':<24} {'field':<8} {'status':<12} evidence")
        for r in results:
            print(f"{r['id'][:24]:<24} {r['field']:<8} {r['status']:<12} {r['evidence']}")
        counts = {st: sum(r["status"] == st for r in results) for st in ("ok", "mismatch", "unreachable")}
        print(f"[show-links] verify: {counts['ok']} ok, {counts['mismatch']} mismatch, {counts['unreachable']} unreachable")
        if args.fix:
            for r in showlinks.apply_fix(path, results):
                print(f"[show-links] blanked {r['id']}.{r['field']} (was {r['value']})")
        elif counts["mismatch"]:
            print("[show-links] nothing edited; rerun with --fix to blank the mismatches")
        return
    changed = showlinks.fill(Path(args.sources), path, set(args.only.split(",")) if args.only else None,
                              ytsearch=args.ytsearch, websearch=args.websearch)
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
    after_stage(Path(args.report).resolve().parent, ingest=True)
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


def status_cmd(args) -> None:
    from . import status as st

    run = Path(args.run)
    info = st.inspect_run(run, arg=args.run, weekly=True if args.weekly else None)
    if args.record:
        info["recorded"] = st.record_boundaries(run, info)
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(st.render_text(info))
        if info.get("recorded"):
            print(f"[status] recorded timing: {', '.join(info['recorded'])}")


def today_name(now=None) -> str:
    """Today's run name in the machine's local timezone."""
    from datetime import datetime

    return (now or datetime.now().astimezone()).strftime("%Y-%m-%d")


def today_cmd(args) -> None:
    name = args.run_name or today_name()
    run_dir = Path(args.runs) / name
    if not (run_dir / "manifest.json").exists():
        print(f"[today] no run {run_dir}; starting daily")
        main(["daily", "--hours", str(args.hours), "--run-name", name, "--runs", str(args.runs)])
    arg = f"runs/{name}" if Path(args.runs).resolve() == ROOT / "runs" else str(run_dir)
    status_cmd(argparse.Namespace(run=arg if Path(arg).exists() else str(run_dir), weekly=False, record=True, json=args.json))


def doctor_cmd(args) -> None:
    from . import doctor

    rows = doctor.run_checks()
    for level, name, msg in rows:
        print(f"[doctor] {level:<4} {name}: {msg}")
    fails = sum(1 for r in rows if r[0] == "fail")
    print(f"[doctor] {len(rows) - fails - sum(1 for r in rows if r[0] == 'warn')} ok, "
          f"{sum(1 for r in rows if r[0] == 'warn')} warn, {fails} fail")
    if fails:
        sys.exit(1)


def library_cmd(args) -> None:
    from . import library

    db = args.db or library.DEFAULT_DB
    if args.lib_cmd != "ingest" and not Path(db).exists():
        sys.exit(f"[library] no library at {db}; run podcast-alpha library ingest runs/<name> (or --all) first")
    if args.lib_cmd == "ingest":
        dirs = library.run_dirs(args.runs) if args.all else [Path(r) for r in args.run]
        if not dirs:
            sys.exit("[library] no run folders given (pass runs/<name> or --all)")
        for d in dirs:
            try:
                r = library.ingest_run(d, db, transcripts=not args.no_transcripts)
            except FileNotFoundError as exc:
                sys.exit(f"[library] {exc}")
            print(f"[library] {r['run']}: {r['episodes']} episodes, {r['claims']} claims, {r['chunks']} transcript chunks -> {db}")
    elif args.lib_cmd == "query":
        try:
            if args.transcripts:
                if not args.text:
                    sys.exit("[library] --transcripts needs --text")
                rows = library.search_transcripts(db, args.text, args.since, args.until, args.person, args.limit)
            else:
                rows = library.query_claims(db, args.ticker, args.person, args.text, args.since, args.until, args.limit)
        except ValueError as exc:
            sys.exit(f"[library] {exc}")
        if args.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
            return
        for r in rows:
            mm, ss = divmod(int(r["timestamp"] or 0), 60)
            if args.transcripts:
                body = f"[{mm}:{ss:02d}] {r['snippet']}"
            else:
                body = f"{r['speaker'] or 'unknown'} [{mm}:{ss:02d}] {r['text']}" + (f" ({', '.join(r['tickers'])})" if r["tickers"] else "")
            print(f"{r['published']} | {r['show']} | {r['title']}\n  {body}\n  {r['link']}")
        print(f"[library] {len(rows)} result(s)", file=sys.stderr)
    elif args.lib_cmd == "export":
        if args.out:
            with open(args.out, "w") as fh:
                n = library.export_jsonl(db, fh, args.since, args.until)
            print(f"[library] {n} claims -> {args.out}")
        else:
            library.export_jsonl(db, sys.stdout, args.since, args.until)
    else:
        print(json.dumps(library.stats(db), indent=1))


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
    t.add_argument("--no-tier1-floor", action="store_true",
                   help="triage: do not also whisper every non-news, non-rerun tier-1 episode")
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
    d.add_argument("--no-tier1-floor", action="store_true", help="queued transcribe: no extra whisper floor for tier-1 episodes")
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
    ep = sub.add_parser("export-published", help="copy a run's report/ranking outputs into published/<run> (committed to git)")
    ep.add_argument("run")
    ep.add_argument("--name", help="folder name under published/ (default: the run folder's name)")
    rp = sub.add_parser("rank-prep", help="weekly: condense transcripts and split them into reader batches under <run>/rank")
    rp.add_argument("run")
    rp.add_argument("--batches", type=int, default=4)
    rm = sub.add_parser("rank-merge", help="weekly: merge reader outputs (rank/batch-*-out.json) into <run>/ranked.json")
    rm.add_argument("run")
    rm.add_argument("--ranked-by", default="Claude")
    rm.add_argument("--week-of", help="YYYY-MM-DD (default: earliest published date in the run)")
    sl = sub.add_parser("show-links", help="fill blank per-podcast links (site, YouTube, Apple, Spotify) in show_links.yaml")
    sl.add_argument("--sources", default=str(ROOT / "sources.yaml"))
    sl.add_argument("--file", default=str(ROOT / "show_links.yaml"))
    sl.add_argument("--only", help="comma-separated source ids")
    sl.add_argument("--ytsearch", action="store_true", help="also search YouTube (yt-dlp) for blank channels; Mac/residential IP only")
    sl.add_argument("--websearch", action="store_true",
                    help="also look for blank Spotify pages with a free DuckDuckGo search (stops at the first rate-limit page); results are title-checked")
    sl.add_argument("--verify", action="store_true",
                    help="check set YouTube channels (>= 2 of the last 15 videos match recent feed titles) and Spotify pages; prints a table, edits nothing")
    sl.add_argument("--fix", action="store_true", help="with --verify: blank the values that are clearly wrong (mismatch, never unreachable)")
    mk = sub.add_parser("mark", help="record a stage start/end timestamp in the run's manifest.json")
    mk.add_argument("stage", choices=["fetch", "transcribe", "select", "report", "publish"])
    mk.add_argument("event", choices=["start", "end"])
    mk.add_argument("--run", required=True)
    st = sub.add_parser("stats", help="trend table across runs: stage durations, e2e, transcribed, recall")
    st.add_argument("--runs", default=str(ROOT / "runs"))
    lib = sub.add_parser("library", help="durable insight library: ingest runs, query claims and transcripts, export")
    lsub = lib.add_subparsers(dest="lib_cmd", required=True)
    ldb = argparse.ArgumentParser(add_help=False)
    ldb.add_argument("--db", help="library database (default data/library.sqlite)")
    li = lsub.add_parser("ingest", parents=[ldb], help="add runs to the library (idempotent; replaces a run's rows)")
    li.add_argument("run", nargs="*", help="run folders, e.g. runs/2026-10-23")
    li.add_argument("--all", action="store_true", help="every folder under --runs")
    li.add_argument("--runs", default=str(ROOT / "runs"))
    li.add_argument("--no-transcripts", action="store_true", help="claims only; skip the transcript text index")
    lq = lsub.add_parser("query", parents=[ldb], help="find claims (or transcript passages with --transcripts)")
    lq.add_argument("--ticker")
    lq.add_argument("--person", help="speaker or episode title contains this")
    lq.add_argument("--text", help="words to find; \"quote\" a phrase")
    lq.add_argument("--since", help="YYYY-MM-DD, by episode publish date")
    lq.add_argument("--until")
    lq.add_argument("--limit", type=int, default=20)
    lq.add_argument("--transcripts", action="store_true", help="search transcript text (needs --text)")
    lq.add_argument("--json", action="store_true")
    le = lsub.add_parser("export", parents=[ldb], help="all claims as JSON lines")
    le.add_argument("--since")
    le.add_argument("--until")
    le.add_argument("--out", help="file (default stdout)")
    lsub.add_parser("stats", parents=[ldb], help="what the library holds")
    p = sub.add_parser("publish", help="validate ranked.json and render the digest")
    p.add_argument("ranked")
    p.add_argument("--out")
    p.add_argument("--top-claims", type=int, default=3)
    p.add_argument("--top", type=int, help="digest only the top N ranked episodes (ranked.json keeps all)")
    p.add_argument("--notion-parent", help="Notion parent page id (API publish needs NOTION_TOKEN)")
    stp = sub.add_parser("status", help="where is this run, and what is the single next action (daily or weekly; read-only)")
    stp.add_argument("run")
    stp.add_argument("--json", action="store_true", help="machine-readable output (schema/status.schema.json)")
    stp.add_argument("--weekly", action="store_true", help="force the weekly (rank) flow instead of auto-detecting")
    stp.add_argument("--record", action="store_true", help="also stamp select/report start/end timing the code can observe (the only write)")
    tdp = sub.add_parser("today", help="start today's daily run if it does not exist, else show its status and next action")
    tdp.add_argument("--hours", type=float, default=48)
    tdp.add_argument("--run-name", help="override the run name (default: today's local date)")
    tdp.add_argument("--runs", default=str(ROOT / "runs"))
    tdp.add_argument("--json", action="store_true")
    sub.add_parser("doctor", help="check python, whisper, ffmpeg, yt-dlp, network, NOTION_TOKEN, data/ and disk")
    args = ap.parse_args(argv)
    own = {"status": status_cmd, "today": today_cmd, "doctor": doctor_cmd}
    if args.cmd in own:
        return own[args.cmd](args)
    {"fetch": fetch, "daily": daily, "merge": merge, "transcribe": transcribe_run, "publish": publish, "check": check,
     "report": render_report, "tmtb-import": tmtb_import, "links": links, "show-links": show_links, "rank-prep": rank_prep, "rank-merge": rank_merge, "transcript-links": transcript_links_cmd, "export-published": export_published, "mark": mark, "stats": stats, "library": library_cmd}[args.cmd](args)


if __name__ == "__main__":
    main()
