# v1.0.0 Release Checklist

## Release target

- Package/plugin version: `1.0.0`
- Distribution model: open-source repository plus Codex and Claude Code plugin manifests
- Runtime model: independent research and odds providers

## Automated release gates

- [x] Provider contract exists for recent results, player form, and odds.
- [x] Research and odds selection is centralized in `provider_router.py`.
- [x] Legacy `--source-mode` cannot override independent provider configuration.
- [x] Sportradar research and The Odds API odds can be combined.
- [x] Crawler research and crawler odds are capability-specific and optional.
- [x] Missing provider configuration produces explicit fallback/skip diagnostics.
- [x] Success and failure summaries share the stable public output contract.
- [x] Offline demo requires no network or API key.
- [x] Runtime dependency declarations include the imported HTTP clients.
- [x] README provides a five-minute keyless quick start.
- [x] Architecture, security, plugin usage, source policy, JSON Schema, and release notes exist.
- [x] Codex and Claude Code manifests use repository-local skill paths.
- [x] `.env.example` contains placeholders only and independent provider settings.
- [x] `python -m compileall app src scripts tests` passes.
- [x] `python -m pytest -q` passes (`42 passed`).
- [x] Offline demo validation passes.
- [x] Equivalent wheel build validation passes (`world_cup_agent_open-1.0.0-py3-none-any.whl`).
- [x] Installed console entry points pass:
  - `world-cup-agent-demo --compact`
  - `world-cup-research-backfill --help`
  - `world-cup-post-match-sync --help`
- [x] Release-facing JSON/TOML files parse successfully.
- [x] Secret and private-path scan passes outside ignored local artifacts.

- [x] Additive pre-match prediction and player-appearance tables are covered by tests.
- [x] Post-match CLI dry-run and idempotency are covered by deterministic tests.
- [x] Missing player minutes remain null and report partial quality.
- [x] Open package contains no private scheduler or delivery integration.
- [x] Final wheel contains no `.env`, `outputs/`, local Mission files, or private runtime modules.
- [x] Final secret and private-machine-path scans return no findings.

## Clean-clone gates

Run these from a clean clone, not the current working directory:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m compileall app src scripts tests
python -m pytest -q
python scripts/run_demo.py
python -m build
```

Confirm the source distribution/wheel or repository release does not contain:

- `.env`
- API keys or tokens
- `outputs/`
- local SQLite databases
- crawler checkouts or browser profiles
- private machine paths
- `__pycache__`, `.pytest_cache`, `.ruff_cache`
- stale build directories or editable-install metadata

## Live provider gates

These require operator-supplied credentials and a research database seeded with the target fixture.

### Paid hybrid

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<live-key>
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<live-key>
```

Acceptance:

- [ ] command completes with `status` equal to `ok` or an accurately explained `partial`
- [ ] `source.research_provider` is `sportradar_soccer`
- [ ] `source.odds_provider` is `the_odds_api`
- [ ] recent-results quality is not falsely reported as `ok` with zero rows
- [ ] player mapping failures are visible as partial/missing
- [ ] odds event omissions or quota errors are visible

### Self-hosted crawler

```dotenv
DEFAULT_RESEARCH_PROVIDER=crawler
DEFAULT_ODDS_PROVIDER=crawler
ENABLE_CRAWLER=true
SPORTS_STABLE_CRAWL_SCRIPTS_DIR=<reviewed-scripts-directory>
CRAWLER_PYTHON_PATH=<optional-python-with-crawl4ai>
```

Acceptance:

- [ ] research crawler capability resolves only when `whoscored_workflow.py` exists
- [ ] odds crawler capability resolves only when `soccerway_odds.py` exists
- [ ] crawler code/revision has been reviewed by the operator
- [ ] source terms and applicable collection rules have been checked

## Host installation gates

### Codex

- [ ] Publish or clone the final repository at its permanent location.
- [ ] Add `.agents/plugins/marketplace.json` as a marketplace source.
- [ ] Install `world-cup-agent-open`.
- [ ] Confirm `skills/world-cup-research-backfill/` is exposed.
- [ ] Run the keyless demo through the installed plugin workflow.
- [ ] Run one target-fixture backfill with explicitly configured providers.

### Claude Code

- [ ] Publish or clone the final repository at its permanent location.
- [ ] Add `.claude-plugin/marketplace.json` as a marketplace source.
- [ ] Install `world-cup-agent-open`.
- [ ] Confirm `skills/world-cup-research-backfill/` is exposed.
- [ ] Run the keyless demo through the installed plugin workflow.
- [ ] Run one target-fixture backfill with explicitly configured providers.

## Publication gates

- [ ] Final remote repository coordinates are known.
- [ ] Marketplace source coordinates are updated if the host requires remote coordinates rather than local `./` sources.
- [ ] Repository visibility and MIT license are confirmed.
- [ ] Private security advisories or another private reporting channel are enabled.
- [ ] Release notes are attached to tag `v1.0.0`.
- [ ] Clean-clone, live-provider, and both host-install gates are recorded.
- [ ] Only after all applicable gates pass: create tag/release and submit marketplace listings.

## Go/no-go rule

Automated local checks can establish repository release readiness. They cannot establish successful marketplace publication. The final go decision requires clean-clone validation, live provider evidence for claimed paths, and host installation checks.
