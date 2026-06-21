# Lineup / News Source Matrix

Use this matrix only for the `lineup_integrity`, `injury_status`, `key_player_status`, and `motivation_stage` supplement. It is not the primary path for `recent_results`, `team_strength`, `players`, `squads`, or `player_form`.

## Source Priority

1. `Sports Mole`
   - Best target pages:
     - match preview pages with `prediction-team-news-lineups`
     - team-specific `predicted-lineups/...-lineup-vs-...`
   - Why:
     - page titles and section headings usually expose `team news`, `predicted lineup`, and player-availability terms explicitly
   - Caveat:
     - this machine previously hit Windows encoding failures before the crawler UTF-8 fix; re-test when changing the wrapper

2. `Sports Illustrated (SI)`
   - Best target pages:
     - `preview-predictions-lineups`
     - `how-to-watch` pages only if they also contain lineup or team-news blocks
   - Why:
     - titles usually expose `Preview, Predictions and Lineups`
   - Caveat:
     - article body can still include heavy site chrome; keep it behind Sports Mole in the priority order

3. `Yahoo Sports`
   - Best target pages:
     - `predicted-lineup` articles
   - Why:
     - title signal is usually usable even when article chrome is noisy
   - Caveat:
     - body extraction is noisy; use only if Sports Mole and SI are unavailable or blocked

4. `RotoWire`
   - Best target pages:
     - `preview-predicted-lineups-team-news`
   - Why:
     - reliable page availability on this machine
     - the local crawler wrapper now condenses the article around lineup/news anchors instead of keeping the raw top-of-page navigation excerpt
   - Caveat:
     - even after cleanup, widget and preview chrome can remain, so treat it as fallback after the higher-priority editorial preview sites

## What To Extract

- Prefer pages whose title or visible body contains:
  - `predicted lineup`
  - `possible lineup`
  - `starting XI`
  - `team news`
  - `injury`
  - `suspended`
  - `doubtful`
- Do not use generic live blogs, scoreboard pages, or betting-only pages as the first choice for lineup/news capture.

## Operational Rule

- For each fixture, try at most two editorial sources before falling back.
- Preserve the exact target URLs used in the operator log or the saved snapshot.
- If only a fallback source succeeds, record that the lineup signal is `partial`, not `ok`.
