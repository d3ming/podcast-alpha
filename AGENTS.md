# AGENTS.md

Daily podcast intel. Code fetches, transcribes and renders; the agent selects and writes the report. Free sources only:
RSS, iTunes Search, faster-whisper on CPU. No API keys except optional Notion.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,whisper]'
.venv/bin/pytest -q
```

Commands below use `pa` for `.venv/bin/python -m podcast_alpha.cli`.

## Layout

- `sources.yaml` shows and free keynote/earnings-call feeds; `people.yaml` people watchlist (no holdings).
- `runs/<name>/` (git-ignored): `manifest.json`, one `.md` per episode, `transcribe.log`, then agent outputs.
- `data/podcast_alpha.sqlite` dedupe DB. `prompts/` agent instructions. `schema/` JSON schemas.

## Daily loop

1. `pa daily --hours 48 --run-name <YYYY-MM-DD>`: people search first, then channels; queues `transcribe` in the
   background (log: `runs/<name>/transcribe.log`). Wait for `finished` in the log, or rerun
   `pa transcribe runs/<name>` (resumable; `--minutes` budget, default 600).
2. Optional: `pa tmtb-import email.txt --run runs/<name>` (or `-` for stdin) with a pasted reference email body.
   Writes `seeds.json`, `missed.json`, `missed.md`. Never scrape Substack.
3. Select: follow `prompts/select.md`, write `runs/<name>/selected.json`, check with `pa check selected <file>`.
4. Report: follow `prompts/report.md`, write `runs/<name>/report.json` (`schema/report.schema.json`).
5. Render/publish: `pa report runs/<name>/report.json` writes `report.md`; with `NOTION_TOKEN` and a parent page
   (`NOTION_PARENT_PAGE_ID`, else `notion_parent_page_id` in `publish.yaml`, else `--notion-parent`) it also creates
   the Notion page. Without a token, publish `report.md` with a Notion connector.

The weekly ranking path (`fetch`, `prompts/rank.md`, `publish`) and the Mac YouTube fallback are in README.md.

## Rules

- Public repo: never commit run folders, holdings, weights, tokens or copied newsletter text.
- Every report claim carries a speaker and a transcript timestamp; paraphrase, don't quote at length.
