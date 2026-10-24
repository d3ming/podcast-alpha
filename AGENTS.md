# AGENTS.md

Operating manual for the agent that runs podcast-alpha, and for anyone changing its code. Read
[docs/architecture.md](docs/architecture.md) once for how the parts fit; [docs/reference.md](docs/reference.md) has every
command and flag.

## Run it

- **Daily:** follow [prompts/daily.md](prompts/daily.md). It starts with `podcast-alpha today` and then loops on
  `podcast-alpha status runs/<name> --json --record`, doing the `next` action each time.
- **Weekly:** follow [prompts/weekly.md](prompts/weekly.md).
- **Environment:** `podcast-alpha doctor` checks everything in a few seconds. Run it first on a new machine.

Use the `podcast-alpha` from the venv that has a Whisper backend (`.venv/bin/podcast-alpha`, or
`/workspace/pa-venv/bin/podcast-alpha` on the Linux server). On Apple silicon install `.[dev,whisper,mac]`.

## Where it runs

- Default: Dong's Mac, weekdays 04:00 via launchd, MLX whisper (`--backend mlx`; `auto` picks MLX on Apple silicon).
  The Mac pushes each run (no audio) to the `runs` branch. See `deploy/mac/README.md`.
- Box (Cain): `podcast-alpha fallback-check` at ~4:45 pulls the Mac's run, or runs `daily` here if the Mac's heartbeat
  is missing, failed or stale. At 9:00, `pull-run --run <date>`, then select, report and publish.
- Never commit run data to main. Only `deliver` writes the `runs` branch.

## Rules

- **Public repo.** Commit `published/<run>/` after `report` or `publish` writes it. Never commit `runs/`, `data/`,
  transcripts, holdings, portfolio weights, tokens, or copied newsletter text.
- **Push straight to `main`.** No branches or pull requests. Run `.venv/bin/pytest -q` first; commit style is in
  [CONTRIBUTING.md](CONTRIBUTING.md).
- **Every claim is grounded.** A speaker and a transcript timestamp on every claim; paraphrase, never quote 12 or more
  words. Fix the file when a check fails; never edit around a check.
- **Never guess a link.** Set `youtube_url` only on a title-or-guest and date match; code verifies it again.
- **Write `manifest.json` only through `podcast_alpha.manifest.update`.** `transcribe` runs for hours while other
  commands update the same file.

## Gotchas

- **Wrong interpreter.** On the Linux server, the `podcast-alpha` on `PATH` is a system-Python shim with no Whisper.
  `daily` and `status` pin the right interpreter for you; call the venv's binary yourself.
- **YouTube blocks server IPs.** `--youtube-mirror`, `links --ytsearch`, `show-links --ytsearch` and
  `check --captions` only work from a residential IP. After the first block, a run stops asking YouTube.
- **A short "transcript" is a show description.** Under 5 KB, it is marked `fallback_text` and whispered.
- **Transcribe is still running.** `status` says `wait` until the log says `finished`. Selecting earlier misses the
  episodes still in the queue.
- **Recall is a next-morning job.** TMTB Podcast Intel, the newsletter we benchmark our picks against, arrives about
  2:40 PM PT, after the 9 AM report, so `tmtb-import` checks yesterday's run. Paste the email; never scrape Substack.
- **Whisper misspells names** ("Nescape" for Netscape) and never labels speakers. Check names against the episode
  title and description, and use a role ("host", "CEO") when the speaker is unclear.
- **A `warning:` about a number** almost always means the cited number or the timestamp is wrong. Fix the cite.
- **Same episode, two feeds.** Weekly `rank-merge` drops the copy with the same transcript; daily `check` allows one
  pick per person.
- **Scripts that call `fetch` or `daily`** need an `if __name__ == "__main__":` guard, because feed parsing uses a
  process pool. The `podcast-alpha` command already has one.

## Develop

```bash
.venv/bin/pytest -q    # about 2 seconds, no network
```

Tests never touch the network, `data/` or `published/`: `conftest.py` points the feed cache at a temp folder and turns
off the automatic library ingest and `published/` export. Keep it that way in new tests.
