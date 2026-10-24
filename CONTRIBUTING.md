# Contributing

Issues and pull requests are welcome. Maintainers and the operator agent commit straight to `main`.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,whisper]'     # on Apple silicon: '.[dev,whisper,mac]'
.venv/bin/pytest -q
```

The suite runs in about 2 seconds with no network access. A change is ready when it passes, and when anything that
changed behavior has a test.

## Ground rules

- **Code stays deterministic.** No LLM calls and no paid APIs. Judgment belongs in `prompts/`, and every output an
  agent writes gets a schema in `schema/` and a check in code.
- **Tests stay offline and isolated.** Use fixtures and fakes for network calls. `conftest.py` keeps tests out of
  `data/` and `published/`; do not work around it.
- **Nothing private in the repo.** No transcripts, holdings, tokens, Notion page ids, personal paths, or copied
  newsletter text. Real-world test fixtures are synthetic or rephrased.
- **Docs move with the code.** A new command or flag goes in [docs/reference.md](docs/reference.md); a new rule for
  the operator goes in [AGENTS.md](AGENTS.md) only if the agent would otherwise get it wrong.

## Commit messages

Write the subject as an imperative sentence about the change's effect, under about 72 characters, with no trailing
period. Use the body to say why and what changed in behavior, wrapped at 72 characters. Name what you verified.

```text
Match Apple episodes by guid before title

A loose title match attached Thursday's TBPN episode to Friday's and
Part 2 of an interview to Part 1. Match the feed guid or enclosure
first; fall back to a 0.9 title ratio with equal numbers and the
closest release date. No match now means no link.

Tests: 257 passed; new cases for both wrong matches.
```

Good subjects: `Cache feeds with conditional GET`, `Match Apple episodes by guid before title`. Avoid `fix stuff`,
`updates`, or a list of file names. Data commits from the operator (`Publish daily report 2026-10-24`) need no body.
