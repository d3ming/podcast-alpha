# Daily runbook

Goal: today's report, published and committed, with no human input. Run from the repo root with the venv's
`podcast-alpha` (see AGENTS.md). `status` tells you what is next; do not guess the order.

1. Run `podcast-alpha today`. It starts today's run if it does not exist, then prints the status.
2. Loop: run `podcast-alpha status runs/<name> --json --record` and act on `next`:
   - `wait`: transcription is running. Wait about 5 minutes, then run status again.
   - `command`: run `next.command` exactly, then run status again.
   - `agent`: follow the prompt file in `next.prompt` (`prompts/select.md` to pick, `prompts/report.md` for links and
     the report), write the file it names, then run `next.command` to validate it.
   - `done`: stop.
3. `blockers` lists validation errors. Fix the file and rerun the command; never edit around a check.
4. Stage notes:
   - **Select:** if `missed.md` exists, read it first.
   - **Links:** find each pick's YouTube video by web search. Set `youtube_url` only on a title-or-guest and date
     match, then run `podcast-alpha links runs/<name>`.
   - **Publish:** without `NOTION_TOKEN`, publish `report.md` through your Notion connector to the page your operator
     configured (it is not in this repo), then run `podcast-alpha mark publish end --run runs/<name>`.
   - **Commit:** `report` writes `published/<name>/`. Commit and push it to `main`:
     `git add published/<name> && git commit -m "Publish daily report <name>" && git push`. Never add `runs/`.
5. Next morning, if you have the TMTB Podcast Intel newsletter (the outside list of essential episodes we benchmark
   against): `podcast-alpha tmtb-import <email.txt>` records recall, the share of its episodes our picks covered.

If stuck:
- `status` keeps saying transcribe is interrupted: run the `next.command` it prints; add `--force` only if
  `transcribe.lock` names a process that is no longer running.
- Whisper or another tool is missing: `podcast-alpha doctor` names it and exits 1.
- A check fails and you cannot fix the file: stop and report the `blockers` lines instead of publishing.

Finish by saying where `report.md` is, what you committed, and any `notes` the last status printed.
