# Plugin Usage

The repo is packaged as a World Cup prediction plugin. The internal backfill command remains available as an auxiliary data-repair tool because it prepares the local prematch dataset used by the prediction algorithm.

## Repository layout

The repository root is the plugin root. Do not copy an individual marketplace JSON file to another directory before installation because both marketplace manifests use `./` as the plugin source.

Required paths:

```text
.codex-plugin/plugin.json
.claude-plugin/plugin.json
.agents/plugins/marketplace.json
.claude-plugin/marketplace.json
skills/world-cup-prediction/
skills/world-cup-research-backfill/
```

## Pre-install verification

From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/run_demo.py
world-cup-predict --help
world-cup-research-backfill --help
```

The demo must succeed without keys. A live prediction run additionally requires an existing research database containing the target fixtures and at least one configured provider path.

## Codex local installation

1. Add `.agents/plugins/marketplace.json` from this repository as a local marketplace.
2. Select `world-cup-agent-open`.
3. Confirm that the installed plugin exposes `skills/world-cup-prediction/`.
4. Ask the host to run the keyless demo first or predict a test fixture.

The exact UI/CLI wording depends on the installed Codex host version. The repository supplies the manifest and local source layout; it does not automate host permissions or marketplace publication.

## Claude Code local installation

1. Add `.claude-plugin/marketplace.json` from this repository as a local marketplace.
2. Select `world-cup-agent-open`.
3. Confirm that the installed plugin exposes `skills/world-cup-prediction/`.
4. Run the keyless demo before providing live provider credentials.

Typical natural-language prompt after installation:

> Help me predict tomorrow's World Cup matches.

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

## Invoke the prediction skill

Typical request to the host:

> Predict fixture `fixture_wc2026_66456916`, explain the probability split, and report the selected research/odds providers plus coverage.

Equivalent local command:

```bash
world-cup-predict --fixture-id fixture_wc2026_66456916
```

Useful options:

- `--db-path`
- `--output-dir`
- `--fixture-id` (repeatable)
- `--local-date`
- `--available-at`
- `--skill-scripts-dir`
- `--crawler-python-path`
- `--no-backfill`
- `--persist`

The lower-level `world-cup-research-backfill` command remains available for data repair, but prediction requests should use `world-cup-predict`.

## Verify prediction output

Check these fields first:

```text
schema_version
status
source.research_provider
source.odds_provider
backfill_error
predictions[].fixture_id
predictions[].probabilities
predictions[].risk
predictions[].coverage
predictions[].calibration
predictions[].gaps
```

Interpretation:

- `ok`: all requested fixtures produced complete open-model prediction fields
- `partial`: prediction completed but at least one provider, coverage, BTTS, staking, or optional field is missing
- `failed`: no requested fixture could produce a prediction payload

## Auxiliary backfill command

Use this only when you need to repair or refresh the local prematch dataset:

```bash
world-cup-research-backfill --fixture-id fixture_wc2026_66456916
```

`--source-mode` is deprecated compatibility metadata. It must not be used to choose providers.

## Post-match sync command

The installed plugin package exposes:

```bash
world-cup-post-match-sync --help
```

Use `--dry-run` on a copied database before the first provider-backed write. The public command persists results and player appearances only; private scheduler and post-match learning/review integrations are not bundled.

Provider configuration is documented in [POST_MATCH_SYNC.md](POST_MATCH_SYNC.md).

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
