# AGENTS.md

Daily podcast intel. Code fetches, transcribes and renders; the agent selects and writes the report. Free sources only:
RSS, iTunes Search, faster-whisper on CPU. No API keys except optional Notion.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,whisper]'
.venv/bin/pytest -q
```

Commands below use the installed `podcast-alpha` entry point. On the box run it from the venv that has
faster-whisper: `/workspace/pa-venv/bin/podcast-alpha` (the `~/.local/bin/podcast-alpha` shim uses system Python, which
has no faster-whisper). `daily` checks this up front and pins the queued `transcribe` to an interpreter that has it
(`PA_PYTHON`, else the current one, else `.venv`, else `/workspace/pa-venv`), and fails fast if none does.

## Layout

- `sources.yaml` shows and free keynote/earnings-call feeds; `people.yaml` people watchlist (no holdings).
- Manifest `url` is the episode page (feed item link, else show page; never a tracking mp3); `audio_url` is the enclosure.
- People with common names take `context:` in `people.yaml`; iTunes hits in sports/fiction/etc. genres are dropped.
- `runs/<name>/` (git-ignored): `manifest.json`, one `.md` per episode, `transcribe.log`, then agent outputs.
- `data/podcast_alpha.sqlite` dedupe DB. `prompts/` agent instructions. `schema/` JSON schemas.

## Daily loop

1. `podcast-alpha daily --hours 48 --run-name <YYYY-MM-DD>`: people search first, then channels; queues `transcribe` in the
   background (log: `runs/<name>/transcribe.log`). Wait for `finished` in the log, or rerun
   `podcast-alpha transcribe runs/<name>` (resumable; `--minutes` budget, default 600).
   Transcripts under 5 KB (`--min-transcript-chars`) are show descriptions: the manifest marks them
   `fallback_text: true`, `transcript_source: fallback`, `has_transcript: false`, and `transcribe` whispers them
   (people first). News/market-recap shows (`news: true` in `sources.yaml`: Halftime, Squawk, Power Lunch, WSJ TNB)
   are whispered last; `--skip-news` skips them. Transcripts already in another run folder (same episode id,
   enclosure, or show + title) are reused, not re-whispered. Episodes outside the window or flagged as replays/re-airs are excluded (`excluded` in the manifest).
2. Recall check, next morning: the TMTB email lands ~2:40 PM PT, after the 9 AM report, so check it against the
   previous day's run: `podcast-alpha tmtb-import email.txt` (defaults to `--date yesterday`, i.e. `runs/<yesterday>`;
   `--run runs/<name>` or `--date YYYY-MM-DD` to override; `-` reads stdin). Writes `seeds.json`, `missed.json`, `missed.md`; fetch or whisper anything
   important in `missed.md` before selecting. Paste or export the email body; never scrape Substack.
3. Select exactly 3 strict picks (novel exec/investor signal, no recaps or news shows): follow `prompts/select.md`, write `runs/<name>/selected.json`, check with `podcast-alpha check selected <file>`.
3b. Links: find each pick's YouTube video by web search and set `youtube_url`/`youtube_published` in `selected.json` only on a
   title-or-guest + date match (never guess), then run `podcast-alpha links runs/<name>` (verifies via oEmbed, adds Apple/
   Spotify/episode-page `listen_url`; see `prompts/report.md`). yt-dlp search from the Mac is manual-only.
4. Report: follow `prompts/report.md`, write `runs/<name>/report.json` (`schema/report.schema.json`).
5. Render/publish: `podcast-alpha report runs/<name>/report.json` writes `report.md`; with `NOTION_TOKEN` and a parent page
   (`NOTION_PARENT_PAGE_ID`, else `notion_parent_page_id` in `publish.yaml`, else `--notion-parent`) it also creates
   the Notion page. Without `NOTION_TOKEN` it skips the API, still writes `report.md` and exits 0; publish `report.md`
   through a Notion connector. `report.md` opens with Key Insights, then Essential Episodes.

The weekly ranking path (`fetch`, `prompts/rank.md`, `publish`) and the Mac YouTube fallback are in README.md.

## Rules

- Public repo: never commit run folders, holdings, weights, tokens or copied newsletter text.
- Every report claim carries a speaker and a transcript timestamp; paraphrase, don't quote at length.

## Timing, triage, recall
- Timings: `fetch` and `transcribe` record their own start/end in `manifest.json` (`timing`). Agent steps call
  `podcast-alpha mark select|report|publish start|end --run runs/<name>`. `timing.e2e` spans first start to last end.
- `podcast-alpha stats` prints a trend table across runs: per-stage durations, e2e, transcribed/total, recall.
- Triage: `transcribe` (and `daily`, which queues it) scores untranscribed episodes on metadata (people.yaml person
  search/name match, tier, named guest, title keywords, recency, news and rerun penalties), stores `triage`
  {score, reasons, rank, whisper} per episode and whispers only the top `--top` (default 8) plus every core-person
  episode. `--top 0` whispers all; `--retriage` recomputes. Deterministic (ties broken by episode_id).
- Recall: `tmtb-import` writes `recall` {tmtb, picks, hits, recall, hit, missed, found_in_run} into the run's
  manifest (our selected.json picks vs TMTB's essential list).

