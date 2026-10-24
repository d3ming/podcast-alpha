# Weekly runbook

Goal: the week's top-15 digest, published and committed. Run from the repo root with the venv's `podcast-alpha`.
After any step, `podcast-alpha status runs/<week> --weekly` shows what is done and the next step.

1. Pick a run name, for example `2026-10-19-weekly` (the week's Monday plus `-weekly`; any unused name works).
   `podcast-alpha fetch --days 7 --run-name <week>`. Add `--youtube-mirror` on a residential IP.
2. `podcast-alpha transcribe runs/<week> --top 0`. This can take hours; it is done when `transcribe.log` says
   `finished`.
3. `podcast-alpha rank-prep runs/<week>` writes `rank/batch-1-in.json` to `rank/batch-4-in.json`.
4. For each batch, one reader (you, or one sub-agent per batch) follows `prompts/rank.md` and writes
   `rank/batch-N-out.json`.
5. `podcast-alpha rank-merge runs/<week>` writes `ranked.json`. On an error, fix the batch file it names and rerun.
6. `podcast-alpha publish runs/<week>/ranked.json --top 15` checks every claim and writes `digest-<week_of>.md`
   (`week_of` is the earliest episode date) and `published/<week>/`. On an `invalid:` line, fix that claim in its
   batch file and rerun from step 5.
7. Without `NOTION_TOKEN`, publish the digest through your Notion connector to the page your operator configured.
8. `git add published/<week> && git commit -m "Publish weekly digest <week>" && git push`.

Done when `status` says `done` and the commit is pushed. Finish by saying where the digest is and how many episodes
were ranked versus fetched.
