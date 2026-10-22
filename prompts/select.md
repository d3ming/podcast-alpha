# Select must-watch episodes (daily)

Input: a daily run folder (`manifest.json` plus one `.md` per episode). Optional: `missed.md` from `tmtb-import`.
Output: `selected.json` with exactly 3 episodes, in the run folder, valid against `schema/selected.schema.json`.
Check it with `podcast-alpha check selected runs/<name>/selected.json`.

Pick exactly 3. The bar is strict: fewer strong picks beat padding, but the file always holds 3, so rank hard.

1. Read every episode's title, description and the first 10 minutes of transcript.
2. A pick must pass all of these:
   - Speaker: an executive on their own company (CEO, CFO, founder, lab head; `kind: exec`) or an investor with a
     named fund view (`kind: investor`). Analysts only with primary research (`kind: analyst`). No `other`.
   - Novel: new numbers, guidance, strategy, product or capital plans not already in the week's coverage.
   - Decision-relevant: could change a view on a named public company, sector or supply chain.
3. Reject: recaps, news and markets shows (daily briefings, anchor-led roundups), clip compilations, hosts-only chat,
   reruns, and repeat appearances that add nothing new. One pick per person.
4. Prefer genuinely new episodes (published under 24 hours before the run). Skip `rerun: true` (replays, re-airs).
5. Prefer episodes with a real transcript (`has_transcript: true`). `fallback_text: true` means the text is a short show
   description, not a transcript: if you still pick it, set `fallback_text: true` and say "fallback" in `why`.
6. `guests`: names and roles as the episode states them. `why`: one sentence naming the new fact and who it affects.
7. Never include holdings, position sizes or weights.
