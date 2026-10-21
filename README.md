# podcast-alpha

Weekly podcast alpha pipeline. Code does the deterministic work (fetch, transcripts, dedupe, validate, render).
A Grok Bot does the judgment (claims, scores, ranking) by following [`prompts/rank.md`](prompts/rank.md).
There are no LLM API keys or calls in this repo.

```
podcast-alpha fetch --days 7          # code: write runs/<date>/ with transcripts and manifest.json
# bot: read the run folder, follow prompts/rank.md, write runs/<date>/ranked.json
podcast-alpha publish runs/<date>/ranked.json   # code: validate, render digest markdown, optional Notion
```

## Setup

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,whisper]'   # pytest and faster-whisper (CPU, int8)
pytest -q
```

Optional: `SUPADATA_API_KEY` enables YouTube channel/person search and transcripts through Supadata. Without it those
steps are skipped and listed under `skipped_sources` in the manifest.

## fetch

`podcast-alpha fetch --days 7 [--only odd-lots,bg2] [--per-source 3] [--refetch]`

- Sources come from [`sources.yaml`](sources.yaml) (9 shows, tiers 1-3, tier 3 capped at 2) and
  [`people.yaml`](people.yaml) (people watchlist, `priority: core|watch`, optional `aliases`; no holdings).
- Each entry has `preferred_source` and `fallback` (defaults at the top of each file):
  - shows: `rss`, then `supadata`, then `ytdlp`. RSS is the default; YouTube is only for video-only content.
  - people: `itunes` (keyless iTunes Search), plus `supadata` YouTube search for priorities in `youtube_priority`
    (default `core`). Podcast Index needs a key and is skipped. Person episodes are tagged `source_id: person:<name>`.
- `transcripts` sets the order: `podcast_transcript` (the feed's tag, also looked up for iTunes hits), `whisper`
  (faster-whisper `base.en`, CPU int8; `--whisper-minutes 120` per run, `--whisper-per-episode 45`), `supadata`,
  `youtube_captions`, `youtube_mirror`.
- Dedupe: SQLite (`data/podcast_alpha.sqlite`) keyed by episode id (RSS guid, else YouTube id). Seen episodes
  are skipped and listed under `skipped_seen`; `--refetch` includes them again.
- Output in `runs/<YYYY-MM-DD>/` (git-ignored):
  - one `.md` per episode: YAML front matter (`episode_id`, `title`, `source`, `source_id`, `tier`, `published`,
    `url`, `youtube_id`, `transcript_source`), the description, then the transcript as `[h:mm:ss] Speaker: text`.
  - `manifest.json`: every episode, including those without a transcript (`has_transcript: false`).

## YouTube fallback off the box

`fetch --youtube-only --days 7 --out <dir>` runs yt-dlp channel and person search with captions into a self-contained
dir (needs only yt-dlp, youtube-transcript-api, pyyaml). `merge <dir> --into runs/<name>` dedupes it into a run.

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
