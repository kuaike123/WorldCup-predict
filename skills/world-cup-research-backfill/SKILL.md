---
name: world-cup-research-backfill
description: Targeted World Cup prematch research backfill for fixtures already present in a local research database. Use when collecting or rebuilding recent results, team strength, players/squads, player-form snapshots, and odds while preserving existing fixture/team IDs and reporting independent provider selection and data quality.
---

# World Cup Research Backfill

Use this skill when the local research database contains target World Cup fixtures but prematch analysis is missing structured research or odds data.

Read [references/source-policy.md](references/source-policy.md) before changing providers, sources, fallback rules, or timestamps. Read [references/lineup-news-source-matrix.md](references/lineup-news-source-matrix.md) before collecting lineup/news evidence.

## Provider contract

Research facts and odds are configured independently:

```dotenv
DEFAULT_RESEARCH_PROVIDER=auto
DEFAULT_ODDS_PROVIDER=auto
ENABLE_CRAWLER=true
```

Research values:

- `auto`
- `sportradar_soccer`
- `crawler`
- `skip`

Odds values:

- `auto`
- `the_odds_api`
- `crawler`
- `skip`

Do not use `--source-mode` to select providers. The option remains only for backward-compatible parsing and is recorded as metadata.

## Workflow

1. Resolve exact target fixtures by fixture ID or local match date.
2. Prepare a narrow staging bundle for only the target fixtures and teams.
3. Resolve research and odds providers through the central router.
4. Collect recent national-team results and player form through the selected research provider.
5. Collect fixture odds through the independently selected odds provider.
6. Import only the facts intended for the existing local database.
7. Read `targeted_backfill_summary.json` and report status, data quality, selected providers, fallback reasons, and readiness blockers.
8. If only lineup integrity remains blocked, follow the lineup/news source matrix rather than broad ad hoc browsing.

## Keyless verification

Before a live backfill, verify the installed package without network access:

```powershell
python scripts\run_demo.py
```

The demo must emit a match summary plus non-empty player-form and odds arrays with `data_quality` set to `ok`.

## Paid-provider example

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<key>
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<key>
```

```powershell
world-cup-research-backfill `
  --db-path outputs\research_local.db `
  --fixture-id fixture_wc2026_66456916
```

## Crawler example

Install and review a compatible crawler separately, then configure:

```dotenv
DEFAULT_RESEARCH_PROVIDER=crawler
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<path-to-crawler-scripts>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

```powershell
world-cup-research-backfill `
  --db-path outputs\research_local.db `
  --fixture-id fixture_wc2026_66456916 `
  --page-timeout-ms 120000
```

The configured scripts directory must contain:

- `whoscored_workflow.py` for research facts
- `soccerway_odds.py` for odds

`--skill-scripts-dir` and `--crawler-python-path` remain explicit per-run overrides.

## Hybrid example

Sportradar research with crawler odds:

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<key>
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<path-to-crawler-scripts>
```

The reverse combination—crawler research plus The Odds API—is also supported.

## Date targeting

```powershell
world-cup-research-backfill `
  --db-path outputs\research_local.db `
  --local-date 2026-06-13
```

## Timestamp rule

- If `--available-at` is omitted, use one minute before the earliest target fixture's three-hour cutoff.
- Preserve that default for prematch reconstruction.
- Override it only when the request explicitly requires another evidence timestamp.

## Output contract

Check `targeted_backfill_summary.json` first. It always contains:

```text
status: ok | partial | failed

data_quality:
  recent_results: ok | partial | missing
  player_form:    ok | partial | missing
  odds:           ok | partial | missing

data:
  recent_results: array
  player_form:    array
  odds:           array

source:
  research_provider
  odds_provider
  route
```

Detailed step diagnostics, import counts, fixture/team IDs, readiness, and failure details remain available.

For failed runs, report `failed_step` and `error.message`. Do not discard partial artifacts that were successfully written before the failure.

## Expected artifacts

- `targeted_backfill_summary.json`
- `recent_results_diagnostics.json` when research collection runs
- `player_form_diagnostics.json` when player-form collection runs
- `odds_diagnostics.json` when odds collection runs
- staged CSV/JSON files used for the local database import

## Guardrails

- Limit the run to requested fixtures.
- Do not change the database schema.
- Do not expose provider credentials.
- Do not silently switch both capabilities because one provider is unavailable.
- Do not treat missing rows as successful coverage.
- Do not use another player's statistics when Sportradar name mapping fails.
- Do not bundle or execute an unreviewed crawler runtime.
- Do not present odds or analysis as a wagering guarantee.
