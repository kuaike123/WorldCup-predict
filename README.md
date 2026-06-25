# World Cup Agent Open

Open-source World Cup research backfill and scoring plugin for Codex and Claude Code.

Bring your own providers, backfill structured prematch facts into a local SQLite database, and emit a machine-readable quality report.

- research: `sportradar_soccer` or user-installed crawler
- odds: `the_odds_api` or user-installed crawler
- output: local SQLite facts plus stable JSON diagnostics

It does **not** place bets, guarantee coverage, or provide financial advice.

## What ships

- targeted World Cup fixture research backfill
- independent provider routing for research facts and odds
- Sportradar Soccer research adapter
- The Odds API odds adapter
- optional user-installed crawler bridge
- local scoring and report helpers
- deterministic keyless demo
- Codex and Claude Code plugin/marketplace manifests

Server APIs, UI, schedulers, push delivery, and automated wagering are intentionally out of scope.

## Five-minute quick start

Python 3.11 or newer is required.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts\run_demo.py
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/run_demo.py
```

The demo is deterministic, uses no network access or API keys, and prints:

- a match summary
- recent-results rows
- player-form snapshots
- an odds snapshot
- per-domain data quality
- explicit source metadata

The installed equivalent is:

```bash
world-cup-agent-demo
```

## Provider model

Research and odds are configured independently. There is no forced global `api` versus `crawler` choice.

| Use case | Research provider | Odds provider |
|---|---|---|
| Paid APIs | `sportradar_soccer` | `the_odds_api` |
| Free/self-hosted | `crawler` | `crawler` |
| Hybrid | `sportradar_soccer` | `crawler` |
| Hybrid | `crawler` | `the_odds_api` |

Copy the sample configuration:

```powershell
copy .env.example .env
```

```bash
cp .env.example .env
```

Primary settings:

```dotenv
DEFAULT_RESEARCH_PROVIDER=auto
DEFAULT_ODDS_PROVIDER=auto
ENABLE_CRAWLER=true
```

Supported values:

- research: `auto`, `sportradar_soccer`, `crawler`, `skip`
- odds: `auto`, `the_odds_api`, `crawler`, `skip`

`auto` prefers the configured paid provider when its key is present, then an installed crawler. Missing or unavailable providers are reported explicitly as `skip`/`missing`; they are not silently treated as successful.

### Paid API configuration

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<key>

DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<key>
```

### Crawler configuration

The crawler runtime is deliberately not bundled. Install a compatible crawler separately, then configure:

```dotenv
DEFAULT_RESEARCH_PROVIDER=crawler
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<path-to-crawler-scripts>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

The scripts directory must contain:

- `whoscored_workflow.py` for research facts
- `soccerway_odds.py` for odds

Provider fallback is capability-specific. A directory with only one of those files can satisfy only that provider capability.

## Run a targeted backfill

The backfill expects a local research database that already contains the target World Cup fixture IDs.

```powershell
world-cup-research-backfill `
  --db-path outputs\research_local.db `
  --fixture-id fixture_wc2026_66456916
```

Or target a local match date:

```powershell
world-cup-research-backfill `
  --db-path outputs\research_local.db `
  --local-date 2026-06-13
```

`--source-mode` remains only for backward-compatible command parsing. It is recorded in diagnostics but **does not select or override providers**. Use `DEFAULT_RESEARCH_PROVIDER` and `DEFAULT_ODDS_PROVIDER` instead.


## Post-match result and player-appearance sync

The package now includes an additive post-match feedback core:

- validated formal predictions can be persisted in `pre_match_predictions`;
- closed scores are written to `match_results`;
- mapped starter/minutes facts are written to `player_match_appearances`;
- the next fixture prefers the latest cutoff-safe real appearance in the existing 30% last-match term;
- the existing aggregate proxy remains the fallback.

Manual dry-run:

```bash
world-cup-post-match-sync \
  --db-path outputs/research_local.db \
  --lookback-hours 48 \
  --dry-run
```

The open package intentionally provides no background scheduler or private post-match review store. See [POST_MATCH_SYNC.md](POST_MATCH_SYNC.md).

## Stable output contract

Every generated `targeted_backfill_summary.json` uses this top-level contract, including failed runs:

```json
{
  "status": "ok | partial | failed",
  "data_quality": {
    "recent_results": "ok | partial | missing",
    "player_form": "ok | partial | missing",
    "odds": "ok | partial | missing"
  },
  "data": {
    "recent_results": [],
    "player_form": [],
    "odds": []
  },
  "source": {
    "research_provider": "sportradar_soccer | crawler | skip",
    "odds_provider": "the_odds_api | crawler | skip"
  }
}
```

Legacy diagnostic fields remain available for compatibility, including selected fixture/team IDs, step details, import counts, readiness, and failure details.

## Plugin usage

The repository root is the plugin root.

- Codex plugin manifest: `.codex-plugin/plugin.json`
- Codex repo marketplace: `.agents/plugins/marketplace.json`
- Claude Code plugin manifest: `.claude-plugin/plugin.json`
- Claude Code repo marketplace: `.claude-plugin/marketplace.json`
- packaged skill: `skills/world-cup-research-backfill/`

See [PLUGIN_USAGE.md](PLUGIN_USAGE.md) for host installation and verification steps.

## Architecture and policy

- [ARCHITECTURE.md](ARCHITECTURE.md): provider boundaries, routing, data flow, and output contract
- [SECURITY.md](SECURITY.md): secrets, crawler trust boundary, vulnerability reporting, and safe operation
- [Source policy](skills/world-cup-research-backfill/references/source-policy.md): approved source matrix and timestamp rules
- [Release checklist](release-checklist.md): v1.0 release gates and manual marketplace checks

## Validation

```bash
python -m compileall app src scripts tests
python -m pytest -q
python scripts/run_demo.py
python -m build
```

`python -m pip wheel . --no-deps` is an acceptable package-build fallback when the `build` module is unavailable.

## Known limitations

- Real Sportradar player-form coverage depends on mapping local shortlist names to Sportradar player IDs. Unmatched players are reported as partial/missing data.
- Live API and crawler coverage cannot be guaranteed; providers can rate-limit, change schemas, or omit events.
- The crawler path executes user-supplied code and browser automation. Install it only from a source you trust and verify site terms before collection.
- Marketplace installation still requires host-side actions and final repository coordinates after publishing to a Git host.
- Odds are informational market data, not a recommendation to wager.

## License

MIT. See [LICENSE](LICENSE).
