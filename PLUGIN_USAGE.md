# Plugin Usage

## Repository layout

The repository root is the plugin root. Do not copy an individual marketplace JSON file to another directory before installation because both marketplace manifests use `./` as the plugin source.

Required paths:

```text
.codex-plugin/plugin.json
.claude-plugin/plugin.json
.agents/plugins/marketplace.json
.claude-plugin/marketplace.json
skills/world-cup-research-backfill/
```

## Pre-install verification

From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/run_demo.py
world-cup-research-backfill --help
```

The demo must succeed without keys. A live targeted backfill additionally requires an existing research database containing the target fixtures and at least one configured provider path.

## Codex local installation

1. Add `.agents/plugins/marketplace.json` from this repository as a local marketplace.
2. Select `world-cup-agent-open`.
3. Confirm that the installed plugin exposes `skills/world-cup-research-backfill/`.
4. Run the skill against a test fixture or ask the host to run the keyless demo first.

The exact UI/CLI wording depends on the installed Codex host version. The repository supplies the manifest and local source layout; it does not automate host permissions or marketplace publication.

## Claude Code local installation

1. Add `.claude-plugin/marketplace.json` from this repository as a local marketplace.
2. Select `world-cup-agent-open`.
3. Confirm that the installed plugin exposes `skills/world-cup-research-backfill/`.
4. Run the keyless demo before providing live provider credentials.

The exact UI/CLI wording depends on the installed Claude Code host version.

## Configure providers

Copy `.env.example` to `.env`, then choose each provider independently.

### Paid providers

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<key>
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<key>
```

### User-installed crawler

```dotenv
DEFAULT_RESEARCH_PROVIDER=crawler
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<scripts-directory>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

### Hybrid example

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<key>
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<scripts-directory>
```

The router records configured and selected providers plus any fallback reason in `source.route`.

## Invoke the skill

Typical request to the host:

> Backfill research for fixture `fixture_wc2026_66456916`, preserve existing rows, and report the selected research/odds providers and `data_quality` from `targeted_backfill_summary.json`.

Equivalent local command:

```bash
world-cup-research-backfill --fixture-id fixture_wc2026_66456916
```

Useful options:

- `--db-path`
- `--output-dir`
- `--fixture-id` (repeatable)
- `--local-date`
- `--available-at`
- `--skill-scripts-dir`
- `--crawler-python-path`
- `--no-resume-existing`

`--source-mode` is deprecated compatibility metadata. It must not be used to choose providers.


## Post-match sync command

The installed plugin package exposes:

```bash
world-cup-post-match-sync --help
```

Use `--dry-run` on a copied database before the first provider-backed write. The public command persists results and player appearances only; private scheduler and post-match learning/review integrations are not bundled.

Provider configuration is documented in [POST_MATCH_SYNC.md](POST_MATCH_SYNC.md).

## Verify output

Check these fields first:

```text
status
data_quality.recent_results
data_quality.player_form
data_quality.odds
source.research_provider
source.odds_provider
source.route
failed_step (failed runs only)
error (failed runs only)
```

Interpretation:

- `ok`: all public data domains are available
- `partial`: command completed but at least one domain is partial or missing
- `failed`: an exception interrupted execution; partial artifacts may still be present

## Publication checklist

Before publishing the repository or submitting a marketplace listing:

1. Replace local marketplace source coordinates only when the final remote repository coordinates are known.
2. Confirm repository visibility and license.
3. Enable a private security-advisory channel.
4. Run all commands in `release-checklist.md` from a clean clone.
5. Test installation in each target host.
6. Verify that no `.env`, API key, local database, output probe, private path, or crawler checkout is included in the release artifact.
7. Tag `v1.0.0` only after the host-install checks pass.

Actual marketplace submission, remote repository publication, and host approval occur outside this codebase.
