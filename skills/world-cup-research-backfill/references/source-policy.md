# Source Policy

## Provider principle

Research facts and odds are independent capabilities. Do not collapse them into a global API-versus-crawler mode.

Primary configuration:

```dotenv
DEFAULT_RESEARCH_PROVIDER=auto
DEFAULT_ODDS_PROVIDER=auto
ENABLE_CRAWLER=true
```

The legacy `--source-mode` CLI option is compatibility metadata only. It must not select or override either provider.

## Approved source matrix

### Recent results

- paid provider: Sportradar Soccer competitor schedules
- self-hosted provider: WhoScored collectors exposed through `whoscored_workflow.py`

### Player form

- paid provider: Sportradar Soccer competitor profiles plus player summaries
- self-hosted provider: WhoScored player fixture collectors exposed through `whoscored_workflow.py`
- local shortlist: `data/research_import/p0_11/core_players.csv`

Sportradar player lookup currently maps the local shortlist to provider players using normalized names. Unmatched players must remain explicit partial or missing data; do not substitute another player's statistics.

### Team strength

- primary: FIFA team/ranking module
- fallback for missing ranking rows: local `p0_11/team_strength_snapshots.json`

### Odds

- paid provider: The Odds API (`h2h`, `spreads`, `totals`)
- self-hosted provider: Soccerway odds collector exposed through `soccerway_odds.py`

### Lineup and news supplement

Follow [lineup-news-source-matrix.md](lineup-news-source-matrix.md). Preferred order remains:

1. Sports Mole
2. Sports Illustrated
3. Yahoo Sports
4. RotoWire

Lineup/news collection is separate from the structured research and odds provider contract.

## Provider availability rules

- `sportradar_soccer` requires `SPORTRADAR_SOCCER_API_KEY`.
- `the_odds_api` requires `THE_ODDS_API_KEY`.
- research crawler requires a configured scripts directory containing `whoscored_workflow.py`.
- odds crawler requires the same configured scripts directory to contain `soccerway_odds.py`.
- `ENABLE_CRAWLER=false` disables crawler selection even when scripts exist.

`auto` prefers the paid provider when its key is present, then the matching crawler capability. An explicitly selected paid provider may fall back to an installed matching crawler when its key is absent. Every fallback is recorded in `source.route`; unavailable capabilities resolve to `skip` and quality `missing`.

## Crawler runtime

The crawler is not bundled with the public plugin. Configure an independently installed and reviewed runtime:

```dotenv
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<crawler-scripts-directory>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

The application loads this path through `app.config.Settings`, including values supplied in `.env`. `--skill-scripts-dir` remains an explicit per-run override.

The generic crawler/news wrapper is separate:

```dotenv
CRAWLER_COMMAND_PATH=python
CRAWLER_COMMAND_ARGS=scripts/crawler_context_wrapper.py
```

Do not use the generic wrapper as the primary structured source for recent results, team strength, player form, or odds.

## Timestamp rule

Formal prematch readiness uses:

```text
available_at <= match_time - 3 hours
```

When `--available-at` is omitted, targeted backfill uses one minute before the earliest target fixture cutoff. Preserve this default for historical prematch reconstruction unless the task explicitly requires another timestamp.

## Output and provenance

Every run must expose:

- selected research provider
- selected odds provider
- configured values and fallback reasons in `source.route`
- `data_quality` for recent results, player form, and odds
- arrays for all three public data domains

Do not report a provider capability as `ok` when no matching data rows exist. Do not hide missing keys, unavailable crawler modules, rate limits, mapping failures, or source omissions.

## Security and compliance

- Never write API keys to files, logs, summaries, fixtures, or diagnostics.
- Treat crawler scripts as executable third-party code.
- Verify provider licenses, target-site terms, robots policies, and applicable law before collecting or redistributing data.
- Odds are informational market data, not a recommendation to wager.

## Known gaps

- player name mapping may reduce Sportradar player-form coverage
- some sources omit goals, assists, or complete market types
- live endpoints can rate-limit, change schemas, or remove events
- lineup, injury, and motivation coverage remains a separate workflow
