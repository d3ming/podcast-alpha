# Agent guide: the podcast-alpha insight library

For an investor agent. The library is a SQLite file (`data/library.sqlite`, built from the `runs/` folders) holding
claims that executives, investors and operators made on podcasts, plus the full transcripts. Use it to answer
"what did people close to the business say about NVDA / HBM / Oracle capex lately", with a source you can cite.

No local data (a cloud agent with only the git checkout)? Read `published/<run>/` instead: `claims.jsonl` (same shape as
`library export`), `episodes.json`, and the report or digest itself. Transcripts are not in git; follow `transcript_url`
or `link` (YouTube jumps to the timestamp).

## What is in it

- **Claims** (`library query`): one falsifiable sentence with speaker, timestamp (seconds into the episode), tickers,
  and a link. Two sources: `ranked` (weekly ranking: tickers, `novelty` 0-3, `verifiable` 0-3, episode `score` 1-10) and
  `report` (daily top-3 picks: no tickers or scores, so `--ticker` can also match a report claim whose text names the
  symbol, see below).
- **Transcript passages** (`library query --transcripts`): about 60-second windows of every transcribed episode, searched
  by text. Use this when no claim was extracted. Not every episode has a transcript (`transcript_path` is null if not).
- **Freshness**: only what has been ingested. Check `library stats` (`date_range`, `runs`). The daily report lands about
  9 AM PT; the weekly ranking after the weekly run. `--since` filters on the episode publish date.

## Commands

```
podcast-alpha library stats
podcast-alpha library query --ticker NVDA --since 2026-10-01 --limit 10 --json
podcast-alpha library query --person "Jassy" --since 2026-10-15
podcast-alpha library query --text "HBM" --since 2026-10-20
podcast-alpha library query --text '"hybrid bonding"' --transcripts --limit 5 --json
podcast-alpha library export --since 2026-10-01 --out feed.jsonl
```

- `--ticker`: tagged claims match the tag exactly, case-insensitive (`NVDA`, `private:Anthropic`). Untagged claims (all
  daily report claims) match only when their text contains the symbol as an upper-case whole word of 3+ characters
  (`NVDA`), or `$SYM` when you pass `--ticker '$AI'`; shorter symbols match untagged text only with the `$` form.
  Company names do not match (`--ticker Oracle` finds nothing; use `ORCL` or `--text Oracle`). Each result has
  `matched_by`: `tag` or `text`; treat `text` hits as likely, not certain. `--person`: matches the speaker or the episode title.
  `--text`: all words must appear; wrap `"a phrase"` in quotes. Filters combine with AND. Default `--limit 20`; `0` is all.
- Without `--json` output is readable text; with it, a JSON array. `export` writes one claim per line (JSONL).
- Ingest (`library ingest runs/<name>` or `--all`) is idempotent; you normally do not need it.

## Result shape

Schema: `schema/library-claim.schema.json`. A claim:

```json
{"episode_id": "rss:cd44123a1d227ebf", "title": "Sundar Pichai on Gemini 4 and the AI compute crunch",
 "show": "Sources with Alex Heath", "published": "2026-10-21", "date": "2026-10-21", "tier": 1,
 "speaker": "Sundar Pichai", "timestamp": 47, "text": "...", "tickers": ["GOOGL"], "novelty": 3, "verifiable": 2,
 "source": "ranked", "run": "weekly-1", "score": 8, "url": "https://...", "youtube_url": "https://www.youtube.com/watch?v=...",
 "transcript_url": "https://...", "link": "https://www.youtube.com/watch?v=...&t=47s", "transcript_path": "/.../episode.md", "matched_by": "tag"}
```

A transcript hit has `episode_id, title, show, published, tier, timestamp, snippet (matches wrapped in **), url,
youtube_url, transcript_url, link, transcript_path`. The same claim can appear twice (once from the report, once from the
weekly ranking); treat that as one source, not two.

## Caveats

- **Checked against transcripts, not filings.** A claim says what was said, not that it is true. Verify numbers
  against filings, earnings calls or data before acting; `verifiable` hints how checkable a claim is.
- **Speakers talk their book.** A CEO praising their product, a VC their portfolio. Note the speaker's stake.
- **Transcripts are machine-made** (whisper or YouTube captions): names, tickers and numbers can be misspelled
  (`Nvidia` vs `in video`). Search variants; confirm a figure by opening `link` at the timestamp.
- Speaker is "unknown" or a role when the transcript does not name them. Timestamps in captions can run a few seconds off.
- Coverage is the shows and people in `sources.yaml` / `people.yaml`; silence is not evidence of absence.

## How to cite

Give **speaker, show and date, timestamp (m:ss), and the `link`** (it already jumps to the moment on YouTube when a video
is known). Example: "Andy Jassy (Amazon CEO), The a16z Show, 2026-10-23, 25:06: 2026 AWS capacity is sold out
(https://www.youtube.com/watch?v=...&t=1506s)". Paraphrase; quote at most a short phrase. If you add your own read,
keep it separate from what was said.
