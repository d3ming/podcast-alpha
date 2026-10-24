# Changelog

## 0.2.0 (2026-10-24)

Makes the pipeline something an agent can run end to end, and its output something other agents can query.

- **Agent driver.** `today`, `status` (the exact next step for the agent) and `doctor`, with runbooks in
  `prompts/daily.md` and `prompts/weekly.md`.
- **Insight library.** `library ingest/query/export/stats` over SQLite full-text search of claims and transcripts.
  `report` and `publish` ingest automatically.
- **Published claims in git.** `report` and `publish` write `published/<run>/` with the agent's JSON, the rendered
  Markdown, episode metadata and `claims.jsonl`.
- **Faster, better transcription on Apple silicon.** MLX `large-v3-turbo` on the GPU (53-58x real time on an M4 Max,
  against 38x for the CPU model), downloads overlapped with transcription, and every tier-1 show always transcribed.
- **Faster fetching.** A conditional-GET feed cache, parsing in a process pool, and parallel iTunes searches: a
  48-hour fetch dropped from about 42 s to 13 s cold and 4 s warm, with identical results.
- **Better links.** Apple episode pages matched by guid or enclosure, YouTube channels verified against their recent
  uploads, more Spotify pages, and a note when a guest appears twice in a digest.
- **Weekly ranking at scale.** `rank-prep` and `rank-merge` split a week across parallel reader agents and merge
  their scores with dedupe and tier rules.

## 0.1.0 (2026-10-21)

First version: daily and weekly runs from free sources, Whisper fallback, schema and grounding checks, Markdown and
Notion output.
