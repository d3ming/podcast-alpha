# Ranking rubric (weekly reader agents)

You are one reader. Input: `rank/batch-N-in.json`, written by `podcast-alpha rank-prep` (episodes with real transcripts,
condensed to a `[timestamp]` every 30 s). Output: `rank/batch-N-out.json`, a JSON list of `{episode_id, title, url, source,
tier, published, score, reason, claims}`, one entry per input episode. Cite timestamps from the condensed text.
`rank-merge` combines the batches into `ranked.json` (`schema/ranked.schema.json`) and rejects ids it does not know;
`publish` rejects timestamps or numbers the transcripts do not support. Merge does not rescale scores, so score against
the absolute anchors below, not against the other episodes in your batch.

## 1. Read and extract claims

For each episode with a transcript, read it and keep the claims an investor could act on or check.
Skip ads, intros, banter and vague opinion. Each claim has:

- `ticker`: list of tickers it is about (`private:Name` for private companies; empty if none).
- `claim`: one falsifiable sentence in your words. No hedging filler.
- `speaker`: name if the transcript or context makes it clear, else role (host, guest), else `unknown`.
- `timestamp`: seconds from the start, taken from the nearest transcript timestamp.
- `novelty` 0-3: 0 widely known, 1 known but updated, 2 uncommon angle, 3 new primary information.
- `verifiable` 0-3: 0 opinion, 1 hard to check, 2 checkable with effort, 3 checkable against filings or public data.

Keep at most 5 claims per episode, best first.

## 2. Score each episode 1-10

Weigh: source tier 20%, novelty 25%, verifiability 20%, specificity (numbers, tickers, mechanisms) 20%,
speaker access (founder or operator > analyst > host) 15%.

Anchors:
- 9-10: new primary information, checkable, material to a ticker. Justify any 9+ in the reason.
- 7-8: strong firsthand or analyst detail with specific numbers.
- 5-6: solid recap of known theses.
- 3-4: thin, mostly opinion, or off-topic for investing.
- 1-2: hype, listicle, or nothing usable.

Sanity check on your batch: a typical week has about the top 10% at 8 or more, the median near 5, and at least 30% at
4 or less. If your scores cluster, re-read the anchors and spread them. Ties are fine, clusters are not.

## 3. Tier rules

- Tier 3 (retail stock YouTube): at most 2 episodes in the ranking. Drop the weaker extras.
- Tier 3 is never a top pick: no tier 3 episode in ranks 1-3, whatever its score.
- No transcript: keep the episode, score it on title, description and tier only, say so in the reason, and give it no claims.

## 4. Write it up

- `reason`: one line (under 240 characters) on why it earned the score.
- Order by score, then by tier, then by recency. `rank` starts at 1.
- Never include holdings, weights or portfolio data in `ranked.json`.
