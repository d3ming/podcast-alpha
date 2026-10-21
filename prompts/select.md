# Select must-watch episodes (daily)

Input: a daily run folder (`manifest.json` plus one `.md` per episode). Optional: `missed.md` from `tmtb-import`.
Output: `selected.json` in the run folder, valid against `schema/selected.schema.json`.
Check it with `podcast-alpha check selected runs/<name>/selected.json`.

1. Read every episode's title, description and the first 10 minutes of transcript.
2. Pick 5-8 episodes. Prefer, in order:
   - executives speaking about their own company (CEO, CFO, founder, lab head): `kind: exec`;
   - investors with a named book or fund view: `kind: investor`;
   - analysts with primary research: `kind: analyst`; anything else is `other` and needs a strong reason.
   At least 4 of the picks should be `exec` or `investor`. Skip recaps, clip compilations and hosts-only chat.
3. Prefer episodes with a transcript (`has_transcript: true`). If a strong pick has none, keep it and say so in `why`.
4. One pick per person unless the second appearance adds new information.
5. `guests`: names and roles as the episode states them. `why`: one sentence on what is new for an investor.
6. Never include holdings, position sizes or weights.
