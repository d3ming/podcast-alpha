# podcast-alpha

MVP of the podcast alpha pipeline: ingest a tiered list of investing podcasts, get transcripts, extract
ticker-linked claims, score claims and episodes with a forced spread, and write a weekly ranking digest
(markdown, optionally a Notion page).

```
podcast-alpha run --days 7
```

## Setup

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
podcast-alpha run --days 7 --no-holdings     # writes out/digest-<week>.md
pytest -q
```

Optional environment variables (names only; nothing is read from files):

| Variable | Effect |
|---|---|
| `XAI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Use that LLM for claim extraction. Without one, a heuristic extractor runs. |
| `PODCAST_ALPHA_MODEL` | Override the model name. |
| `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` | Needed by `--notion`, which publishes the digest as a child page. |

## Pipeline

1. **Sources** (`sources.yaml`): 9 sources in tiers T1 to T3. T3 is capped at 2 episodes per run.
2. **Ingest** (`ingest.py`): RSS via feedparser and YouTube via yt-dlp. Dates come from the publisher feed or the
   video's upload time. Episode ids are the RSS guid, else the YouTube id.
3. **Dedupe** (`store.py`): SQLite `episodes` table keyed by episode id; `claims` table feeds novelty (last 90 days).
   Use `--rescore` to reprocess.
4. **Transcripts** (`transcribe.py`): `podcast:transcript` tag, then YouTube captions, then a YouTube mirror found
   by title match for audio-only feeds. faster-whisper is a stub.
5. **Extraction** (`extract.py`): one JSON record per claim: `ticker, claim, speaker, timestamp, novelty (0-3),
   verifiable (0-3)`. LLM if a key is set, otherwise heuristic (sentences with a known company or cashtag plus a
   number, forecast or financial term).
6. **Scoring** (`score.py`): claims and episodes are scored 1-10 against anchors (9-10 new primary info, checkable,
   material; 5-6 solid recap; 3 or lower hype). Ranks are then forced to a spread: top 10% at 8+, median near 5,
   30% at 4 or less. Episode raw score weights: tier 20%, novelty 25%, verifiability 20%, specificity 20%,
   speaker access 15%.
7. **Holdings** (`holdings.py`): fetches the folio JSON at run time and multiplies relevance by `1 + weight` for
   episodes with claims on a held ticker. Weights stay in memory and are never logged or written. `--no-holdings`
   turns it off.
8. **Output** (`digest.py`, `notion.py`): markdown ranking with score, one-line reason and top claims with
   timestamp links. `--notion` publishes it.

Out of scope for v0.1: claim verification against filings, speaker diarization, scheduling.

## Sample

[`samples/digest-2026-10-14.md`](samples/digest-2026-10-14.md) is a real run from 2026-10-21 with
`--no-holdings` and the heuristic extractor (no LLM key on the build box). It contains no holdings data.

## Known limits

- The heuristic extractor is a placeholder for the LLM step. It misses claims stated without numbers or known
  company names, and speakers are "unknown" unless the transcript labels them.
- YouTube captions can be rate-limited; the fetch retries 3 times then records no transcript.
- Claims are unverified.
