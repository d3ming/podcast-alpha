# Daily report (for the agent writing report.json)

Input: `selected.json` and the episode `.md` files it points to.
Output: `report.json` valid against `schema/report.schema.json`, then `podcast-alpha report runs/<name>/report.json`.

Shape:
- `title`: "Podcast Intel: " plus the main guests and topics, comma separated.
- `key_insights`: 3-5 short strings (under 200 characters), the most decision-relevant takeaways across all three
  episodes, each naming the company or sector affected. They render first, above Essential Episodes.
- `essential`: exactly 3 entries, the selected episodes in priority order. `headline` names the guest, their company and the
  2-3 most useful topics; `show`; `guests`: "host and guest, role".
- `episodes`: exactly 3, one per essential entry, same order:
  - `signal`: 3-4 bullets, the most investable points first. Each has `text` (what was said, in your words),
    `speaker`, `timestamp` and `our_read` (one clause on why it matters: who benefits, who is at risk, what to watch).
  - `sections`: 3-6 numbered topics in episode order. `title` is short; `body` is one paragraph of 80-200 words in
    your own words. Add `claims` for specific numbers, guidance or named companies.

Rules:
- Every `signal` bullet and claim needs `speaker` (name from the transcript, else role, else `unknown`) and
  `timestamp` in seconds from the nearest `[h:mm:ss]` line. Use `null` only when the episode has no transcript.
- Paraphrase. Quote at most a short phrase. No claims that are not in the transcript.
- Flag conflicts of interest the speaker mentions (investments, board seats) in a signal bullet.
- No holdings, weights or position data. No investment advice.
