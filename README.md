# podcast-alpha

Weekly podcast alpha pipeline. Code does the deterministic work (fetch, transcripts, dedupe, validate, render).
A Grok Bot does the judgment (claims, scores, ranking) by following [`prompts/rank.md`](prompts/rank.md).
There are no LLM API keys or calls in this repo.

```
podcast-alpha fetch --days 7          # code: write runs/<date>/ with transcripts and manifest.json
# bot: read the run folder, follow prompts/rank.md, write runs/<date>/ranked.json
podcast-alpha publish runs/<date>/ranked.json   # code: validate, render digest markdown, optional Notion
```

For the daily loop (`daily`, `transcribe`, `tmtb-import`, select, report) see [AGENTS.md](AGENTS.md).

## Setup

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,whisper]'   # pytest and faster-whisper (CPU, int8)
pytest -q
```

## fetch

`podcast-alpha fetch --days 7 [--only odd-lots,bg2] [--per-source 3] [--refetch]`

- Sources come from [`sources.yaml`](sources.yaml) (shows, keynote and earnings-call feeds; tiers 1-3, tier 3 capped at 2) and
  [`people.yaml`](people.yaml) (people watchlist, `priority: core|watch`, optional `aliases`; no holdings).
- Each entry has `preferred_source` and `fallback` (defaults at the top of each file). Everything is free and keyless.
  - shows: `rss` (channel podcasts, keynote feeds, earnings-call feeds). `ytdlp` only runs on the Mac (below).
  - people: `itunes` (iTunes Search). Podcast Index needs a key and is skipped. Person episodes are tagged
    `source_id: person:<name>`.
- `transcripts` sets the order: `podcast_transcript` (the feed's tag, also looked up for iTunes hits), `whisper`,
  `youtube_captions`, `youtube_audio` (yt-dlp bestaudio + whisper), `youtube_mirror`.
- `fetch` does not run whisper by default (`--whisper-minutes 0`); episodes keep `audio_url` in the manifest.

## transcribe

`podcast-alpha transcribe runs/<name> [--minutes 600] [--per-episode N] [--db data/podcast_alpha.sqlite]`

faster-whisper (`base.en`, CPU, int8; `PA_WHISPER_MODEL` overrides) works through episodes without a transcript, whole
episodes by default, until the minute budget runs out. It saves the manifest after each episode, so it is resumable;
failures are recorded as `whisper_error` and skipped next time. A `transcribe.lock` blocks a second job. Run it detached:

```
nohup .venv/bin/python -m podcast_alpha.cli transcribe runs/<name> > runs/<name>/transcribe.log 2>&1 &
```

- Dedupe: SQLite (`data/podcast_alpha.sqlite`) keyed by episode id (RSS guid, else YouTube id). Seen episodes
  are skipped and listed under `skipped_seen`; `--refetch` includes them again.
- Output in `runs/<YYYY-MM-DD>/` (git-ignored):
  - one `.md` per episode: YAML front matter (`episode_id`, `title`, `source`, `source_id`, `tier`, `published`,
    `url`, `youtube_id`, `transcript_source`), the description, then the transcript as `[h:mm:ss] Speaker: text`.
  - `manifest.json`: every episode, including those without a transcript (`has_transcript: false`).

## YouTube fallback on the Mac (manual)

For video-only content, from `~/projects/podcast-alpha` on a residential connection:

```
cd ~/projects/podcast-alpha && git pull -q --ff-only && python3 -m venv .venv && .venv/bin/pip install -q yt-dlp youtube-transcript-api pyyaml faster-whisper 'av<16' && rm -rf /tmp/pa-yt && .venv/bin/python -m podcast_alpha.cli fetch --youtube-only --days 7 --out /tmp/pa-yt/out && tar czf /tmp/pa-yt.tgz -C /tmp/pa-yt out && echo /tmp/pa-yt.tgz
```

YouTube channels and person search run through yt-dlp at a slow pace (`--sleep-requests`, `--sleep-interval`,
`--limit-rate`). Captions are used when available; otherwise bestaudio is downloaded and transcribed with whisper
(skipped if faster-whisper is not installed; `--whisper-minutes` defaults to 600). The output dir is self-contained.
On the box: `podcast-alpha merge <dir> --into runs/<name>` dedupes it against the DB and the run.

## rank (bot)

The bot follows [`prompts/rank.md`](prompts/rank.md): claim fields, 1-10 scores with anchors and a forced spread,
tier rules (tier 3 at most 2 episodes and never in ranks 1-3), a one-line reason and up to 5 top claims per episode.
Output format: [`schema/ranked.schema.json`](schema/ranked.schema.json).

## publish

`podcast-alpha publish runs/<date>/ranked.json [--out file.md] [--notion-parent <page id>]`

- Validates against the schema and the tier rules, and rejects anything that looks like holdings data.
  A clustered score spread prints a warning.
- Writes `digest-<week_of>.md` next to the ranked file.
- If `NOTION_TOKEN` and a parent page id (`--notion-parent` or `NOTION_PARENT_PAGE_ID`) are set, it also creates the
  Notion page through the API. Otherwise Cain publishes the markdown with the Notion connector.

## Holdings

`podcast_alpha/holdings.py` can fetch the folio JSON at run time so a bot can weigh relevance privately.
Holdings and weights never go into the repo, `ranked.json`, the digest or Notion; publish rejects them.

Out of scope for v0.1: claim verification against filings, speaker diarization, scheduling.
