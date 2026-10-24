# Command reference

Every command is a subcommand of `podcast-alpha` (`podcast-alpha <command> --help` lists all flags). Run it from the
venv that has a Whisper backend. Run folders live in `runs/<name>/` and are git-ignored.

## Run a day

| Command | Does |
|---|---|
| `today [--hours 48]` | Starts today's run with `daily` if it does not exist, then prints `status` |
| `status runs/<name> [--json] [--record]` | Prints the stage, blockers and the single next action. `--record` stamps stage timings |
| `doctor` | Checks Python, the Whisper backend, ffmpeg, yt-dlp, network and `NOTION_TOKEN` in a few seconds |
| `daily --hours 48 --run-name <YYYY-MM-DD>` | Searches people, then channels, and queues `transcribe` in the background |
| `transcribe runs/<name>` | Whispers episodes without a transcript, best first, within a minutes budget. Resumable |
| `check runs/<name>/selected.json` | Validates the agent's picks (schema inferred from the file name) |
| `links runs/<name>` | Verifies YouTube links and fills listen links in `selected.json` |
| `report runs/<name>/report.json` | Validates, renders `report.md`, publishes to Notion if configured |
| `tmtb-import <email.txt>` | Next morning: compares yesterday's picks with the TMTB newsletter list |

### daily

- People search runs first, then channels. The queued `transcribe` logs to `runs/<name>/transcribe.log`; it is done
  when the log says `finished`.
- Rerunning with an existing `--run-name` only adds new episodes. It refuses while `transcribe.lock` names a live process.
- Episodes outside the window or flagged as replays or re-airs are marked `excluded` in the manifest.
- A transcript under 5 KB (`--min-transcript-chars`) is really a show description. The manifest marks it
  `fallback_text: true`, `transcript_source: fallback`, `has_transcript: false`, and `transcribe` whispers it.
- A transcript already in another run folder (same episode id, enclosure, or show and title) is reused, not re-whispered.
- `daily` pins the queued `transcribe` to an interpreter that has Whisper: `PA_PYTHON`, else the current one, else
  `.venv`, else `/workspace/pa-venv`. It fails fast if none has it.
- `--youtube-mirror` (residential IP only) captions episodes from the same episode on YouTube.
- Feed errors print at the end. `manifest.sources` holds `{found, kept, error}` per source, and `stats` shows the
  latest run's errors.

### transcribe

`transcribe runs/<name> [--minutes 600] [--top 8] [--per-episode N] [--skip-news] [--no-tier1-floor] [--retriage]`

- **Triage** scores untranscribed episodes on metadata only: a `people.yaml` match, show tier, named guests in the title,
  title keywords, recency, and penalties for news shows and reruns. It whispers the top `--top` (default 8), every
  `core` person's episode, and every tier-1 episode that is not news or a rerun (`--no-tier1-floor` turns that off).
  `--top 0` whispers everything. Ties break on episode id, so triage is deterministic.
- **Budget**: `--minutes` caps audio minutes, spent in triage order. News shows (`news: true` in `sources.yaml`) go
  last; `--skip-news` skips them.
- **Resumable**: the manifest is saved after each episode. Failures are recorded and skipped next time
  (`--retry-errors` retries them). A `transcribe.lock` blocks a second job; `--force` takes over a stale lock.
- **Prefetch**: the next episode downloads while the current one transcribes (`PA_PREFETCH=0` turns it off).
- **Downloads** retry 5xx, 429 and timeouts with backoff (`PA_DOWNLOAD_RETRIES`, `PA_DOWNLOAD_BACKOFF`); a 4xx is final.
  Each failure lands in `manifest.failures` and `transcribe.failed.log`.

Run it detached when calling it by hand:

```bash
nohup .venv/bin/python -m podcast_alpha.cli transcribe runs/<name> >> runs/<name>/transcribe.log 2>&1 &
```

### Whisper backends

| Machine | Backend | Default model | Measured speed |
|---|---|---|---|
| Apple silicon with `.[mac]` | MLX on the GPU | `mlx-community/whisper-large-v3-turbo` | 53-58x real time (M4 Max) |
| Anything else | faster-whisper on the CPU, int8 | `base.en` | 38x real time (M4 Max CPU, 4 threads) |

`PA_WHISPER_BACKEND=mlx|faster|auto` forces one. `PA_WHISPER_MODEL` overrides the model and `PA_WHISPER_THREADS` the
CPU threads (default 4). The manifest's `transcript_source` names the model, for example `whisper large-v3-turbo (52 min)`.

### check, links and report

- `check` grounds the file in the run: episode ids and transcript flags against the manifest, no reruns, one pick per
  person. For a report it also checks that timestamps fall inside the transcript, that no sentence copies 12 or more
  words verbatim, and that the episodes match `selected.json`. `links` and `report` run the same checks and refuse on errors.
- A `warning:` line means a number in a cite is not spoken within 2 minutes of its timestamp. It is usually a wrong
  number or a wrong time; fix the cite. `check report <file> --captions` (residential IP) compares timestamps with the
  YouTube captions.
- `links` accepts a `youtube_url` the agent found only after oEmbed confirms it, and adds a `listen_url` (Apple,
  Spotify or the episode page). `--ytsearch` (residential IP) searches YouTube itself, still oEmbed-verified.
- When an episode page is missing, a tracking link or just the show's homepage, `links` and `publish` look the episode
  up on Apple Podcasts. They match the feed's guid or enclosure first, then a near-exact title (0.9, no differing
  numbers) released within 2 days. No match means no link.
- `report` writes `report.md` (Key Insights first, then Essential Episodes). With `NOTION_TOKEN` and a parent page
  (`NOTION_PARENT_PAGE_ID`, else `notion_parent_page_id` in `publish.yaml`, else `--notion-parent`) it also creates
  the Notion page. Without a token it still writes `report.md` and exits 0.
- `report` and `publish` then ingest the run into the library and write `published/<run>/` (see below).

### tmtb-import

TMTB Podcast Intel is the outside newsletter we benchmark against; recall is the share of its essential episodes our
picks covered. It arrives about 2:40 PM PT, after the 9 AM report, so the check runs the next morning against
yesterday's run: `tmtb-import email.txt` (default `--date yesterday`; `--run runs/<name>` or `--date YYYY-MM-DD`
overrides; `-` reads stdin). It writes `seeds.json`, `missed.json` and `missed.md`, and records `recall` in the
manifest. Paste or export the email body; never scrape Substack.

## Run a week

```bash
podcast-alpha fetch --days 7 --run-name <week>        # add --youtube-mirror on a residential IP
podcast-alpha transcribe runs/<week> --top 0
podcast-alpha rank-prep runs/<week>                   # condensed transcripts in 4 balanced batches
# one reader agent per batch follows prompts/rank.md and writes rank/batch-N-out.json
podcast-alpha rank-merge runs/<week>                  # dedupe, order, tier rules -> ranked.json
podcast-alpha publish runs/<week>/ranked.json --top 15
```

- `rank-prep` keeps a timestamp every 30 seconds, about half the text, and skips episodes without a real transcript.
- `rank-merge` keeps the higher-scored copy when two feeds carry the same transcript, orders by score, then tier, then
  recency, and enforces the tier-3 rules (at most 2, none in the top 3).
- `publish` validates against the schema and tier rules, rejects anything that looks like holdings data, warns on a
  clustered score spread, and runs the same grounding checks as `report`. It writes `digest-<week_of>.md` next to the
  ranked file, with a Transcript line, a Show line and an "Also #N" note when a guest repeats.

## Data and outputs

| Command | Does |
|---|---|
| `library ingest runs/<name>` or `--all` | Loads claims and transcript text into `data/library.sqlite`. Idempotent; never writes into the run |
| `library query --ticker/--person/--text/--since [--transcripts] [--json]` | Searches claims or transcript passages |
| `library export [--since] [--out file.jsonl]` / `library stats` | Flat claims feed / counts and date range |
| `export-published runs/<name> [--name]` | Writes `published/<name>/`; `report` and `publish` do it automatically |
| `stats` | Trend table across runs: stage times, idle time, transcribed share, recall |
| `mark <stage> start|end --run runs/<name>` | Records an agent stage time by hand (`status --record` usually does it) |
| `transcript-links runs/<name>` | Backfills hosted transcript links for older runs |

- `published/<run>/` holds the agent's JSON, the rendered Markdown, `episodes.json` (metadata only) and
  `claims.jsonl`. It is committed to git. Transcripts and show descriptions never are.
- `PA_NO_LIBRARY=1` and `PA_NO_PUBLISHED=1` turn off the automatic ingest and export.
- Timings: `fetch` and `transcribe` stamp themselves in `manifest.timing`. `timing.e2e` spans first start to last end;
  `active_seconds` sums stages; `idle_seconds` runs from transcribe end to select start.

## Shows and links

- `show-links [--only id,id] [--ytsearch] [--websearch]` fills blank fields in `show_links.yaml` from the iTunes
  lookup, the feed and the show's site. Spotify and YouTube candidates must match the show name by page title.
  Set values are never overwritten. Run it after adding a source.
- `show-links --verify [--fix]` prints ok, mismatch or unreachable for each YouTube channel (2 of its last 15 uploads
  must match feed titles) and Spotify page. `--fix` blanks clear mismatches only. The first YouTube rate-limit page
  stops further YouTube requests.

## Fetch details

`fetch --days 7 [--only odd-lots,bg2] [--per-source 3] [--refetch]`

- Each source has a `preferred_source` and a `fallback` (defaults at the top of `sources.yaml` and `people.yaml`).
  Shows use `rss`; people use iTunes Search. Podcast Index needs a key and is skipped.
- `transcripts` sets the order: the feed's `podcast:transcript` tag, YouTube captions, a YouTube mirror, then Whisper.
- Feeds use a conditional-GET cache in `data/feed_cache/` (`PA_FEED_CACHE=<dir>` moves it, empty disables it) and parse
  in a process pool. A failed request is reported as a source error and never answered from the cache. People searches
  run 3 at a time with backoff on 403 and 429.
- The seen-episode database (`data/podcast_alpha.sqlite`) skips episodes already fetched; `--refetch` includes them.
- Each episode `.md` has YAML front matter (`episode_id`, `title`, `source`, `tier`, `published`, `url`, `youtube_id`,
  `transcript_source`), the description, then the transcript as `[h:mm:ss] Speaker: text`.

### YouTube-only content (residential IP)

For video-only shows, fetch on a machine YouTube does not block and merge the output into the run:

```bash
.venv/bin/podcast-alpha fetch --youtube-only --days 7 --out /tmp/pa-yt/out
podcast-alpha merge /tmp/pa-yt/out --into runs/<name>
```

yt-dlp runs slowly on purpose (`--sleep-requests`, `--sleep-interval`, `--limit-rate`). Captions are used when present;
otherwise the audio is whispered.

## Holdings

`podcast_alpha/holdings.py` can fetch a portfolio JSON at run time (`PA_FOLIO_URL`; unset means no weighting) so an
agent can weigh relevance privately. Holdings and weights never go into the repo, `ranked.json`, the digest or Notion;
`publish` rejects them.

## Website

`podcast-alpha site [--out dist]` (or `python3 -m podcast_alpha.site`) renders `published/` into a static site: a home
page with the latest run, one page per run, one page per ticker, an About page, and agent feeds (`/runs.json`,
`/claims.jsonl`, `/runs/<run>/claims.jsonl`). It reads the committed JSON only, uses the standard library, and makes
no network calls. `vercel.json` sets the build command and `dist` as the output; `dist/` is git-ignored.
