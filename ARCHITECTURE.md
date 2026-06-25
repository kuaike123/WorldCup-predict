# Architecture

## System boundary

World Cup Agent Open is a local plugin and Python package. It collects or imports prematch research facts, stores them in a local research database, and exposes scoring/report helpers. It does not include a hosted API, frontend, scheduler, messaging delivery, or wagering execution.

## Provider flow

```text
                    .env / process environment
                              |
                              v
                   app.config.load_settings
                              |
                              v
              app.research_db.provider_router
                  /                         \
                 /                           \
        research selection                odds selection
         /              \                 /             \
SportradarProvider   CrawlerProvider  TheOddsApiProvider  CrawlerProvider
         \              /                 \             /
          \            /                   \           /
           recent_results + player_form      odds snapshots
                         \                   /
                          \                 /
                    targeted backfill bundle
                              |
                              v
                    local research database
                              |
                              v
                feature extraction and scoring
```

Research and odds are separate decisions. The legacy `--source-mode` flag is accepted only as compatibility metadata and is not an input to provider selection.

## Provider contract

`app/research_db/provider_contracts.py` defines the public contract:

```python
class BaseProvider:
    def get_recent_results(self, team_id: str) -> ProviderResult: ...
    def get_player_form(self, player_id: str) -> ProviderResult: ...
    def get_odds(self, match_id: str) -> ProviderResult: ...
```

All operations return `ProviderResult` with:

- `status`: `ok`, `partial`, `missing`, `failed`, or `unsupported`
- `data`: a list of dictionaries
- `diagnostics`: explicit provider/capability/error metadata

A provider must return `unsupported` for an unsupported capability. It must not return an empty success or silently call a different provider.

Concrete implementations:

- `SportradarSoccerProvider`
  - recent results
  - player form
  - team profile discovery used for player mapping
  - odds explicitly unsupported
- `TheOddsApiProvider`
  - odds
  - research capabilities explicitly unsupported
- `CrawlerProvider`
  - optional bridge with injected fetchers
  - missing fetchers return explicit `missing`

The existing batch collectors remain responsible for writing bundle files efficiently. Sportradar batch collection calls the public provider contract rather than the raw HTTP adapter.

## Router rules

`provider_router.resolve_provider_route` is the selection authority.

Research options:

- `auto`
- `sportradar_soccer`
- `crawler`
- `skip`

Odds options:

- `auto`
- `the_odds_api`
- `crawler`
- `skip`

Availability checks are capability-specific:

- research crawler requires `whoscored_workflow.py`
- odds crawler requires `soccerway_odds.py`
- Sportradar requires `SPORTRADAR_SOCCER_API_KEY`
- The Odds API requires `THE_ODDS_API_KEY`

When a configured paid provider lacks its key and the matching crawler capability is installed, the route records an explicit crawler fallback. Otherwise the capability is selected as `skip`, and downstream quality becomes `missing` instead of reporting success.

## Backfill stages

1. Resolve target fixture rows from fixture IDs or local date.
2. Prepare a narrow staging bundle for only the target fixtures and teams.
3. Collect recent national-team results.
4. Collect player-form snapshots.
5. Collect fixture odds.
6. Import supported facts into the local research database.
7. Build readiness and stable public summary output.

Each stage retains detailed diagnostics. A failure writes a summary with `status: failed`, the failed stage, partial step states, and any data already written.

## Stable public output

`targeted_backfill_summary.json` always exposes:

```text
status
  ok | partial | failed

data_quality
  recent_results: ok | partial | missing
  player_form:    ok | partial | missing
  odds:           ok | partial | missing

data
  recent_results: array
  player_form:    array
  odds:           array

source
  research_provider
  odds_provider
  route diagnostics
```

`ok` means all three public data domains are available and their collection step did not report partial coverage. `partial` means the run completed but at least one domain is partial or missing. `failed` means an exception interrupted the backfill.

The schema is documented in `schemas/targeted_backfill_summary.schema.json`.


## Post-match feedback loop

The reusable public core adds two additive tables:

- `pre_match_predictions`: exact validated formal prediction JSON;
- `player_match_appearances`: per-fixture appeared/starter/minutes facts.

```text
formal prediction -> pre_match_predictions
closed provider event -> match_results + player_match_appearances
next fixture -> latest eligible appearance -> key-player 30% last-match term
```

`PostMatchSyncService` is provider-injected and processes fixtures independently. Dry-run performs no writes. Missing minutes remain null and yield partial quality. The open package omits scheduler, bot, delivery, and private review orchestration.

Prediction persistence is explicit: low-level feature/scoring helpers remain pure, while formal orchestration calls `PreMatchResearchScoringService.save_prediction()`. Only payloads implementing the complete formal prediction contract are persisted.

Appearance selection is cutoff-safe: `played_at` must be earlier than the target match and `available_at` must not exceed the feature cutoff.

## Deterministic demo

`app.demo` and `scripts/run_demo.py` implement a fixed offline fixture. The demo:

- performs no network calls
- reads no API keys
- uses a fixed `as_of` timestamp
- returns stable sorted JSON
- exercises match summary, player form, odds, source, and data-quality fields

## Trust boundaries

### Paid APIs

API keys enter only through settings/environment variables. Keys must not be written to summaries, fixtures, logs, or manifests.

### Crawler runtime

The crawler is user-installed executable code and may launch browser automation. The plugin validates expected script names but cannot attest to third-party crawler safety. The crawler should run with least privilege in a dedicated environment.

### Local database

The backfill writes to an operator-selected local SQLite database. v1.0 does not introduce a database migration.

### Plugin hosts

Codex and Claude Code load the repository skill and invoke local commands. Actual marketplace publication and host permissions are controlled outside this repository.

## Testing strategy

- provider contract tests: unsupported/missing/success semantics
- router tests: independent selection, fallback, and legacy flag non-interference
- parser smoke tests: Sportradar match/player extraction
- summary tests: fixed arrays and quality values
- failure tests: stable failed summary
- demo smoke test: keyless deterministic JSON
- manifest tests: local paths and version consistency
- package build test: runtime dependencies and entry points
