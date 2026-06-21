# Release Notes: v0.1.0

## What ships

`world-cup-agent-open` is the trimmed public plugin package for local World Cup research backfill and scoring.

Included:

- research backfill skill with `api` and `crawler` source modes
- local scoring core
- Codex plugin manifest
- Claude Code plugin manifest
- repo-scoped marketplace manifests for both hosts
- release-facing smoke tests for the public contract

Excluded on purpose:

- server, UI, scheduler, push integrations
- bundled crawler runtime
- hosted install automation

## Public contract

- `api` mode uses `THE_ODDS_API_KEY` and only refreshes odds
- `crawler` mode uses `SPORTS_STABLE_CRAWL_SCRIPTS_DIR` and rebuilds crawler-backed research facts
- source-mode resolution order is:
  1. explicit `--source-mode`
  2. `THE_ODDS_API_KEY`
  3. `SPORTS_STABLE_CRAWL_SCRIPTS_DIR`
  4. fail with a config error

## Validation snapshot

Validated in the current workspace:

- release docs and manifests are present
- public smoke tests pass via `python -m pytest tests\test_public_repo_smoke.py -q`
- editable install works via `python -m pip install -e .`
- installed CLI resolves via `world-cup-research-backfill --help`
- smoke coverage also locks the installed CLI help options, repo-root marketplace layout assumptions, and release-facing crawler runtime override docs
- manifest-referenced repo-local plugin paths resolve for both hosts
- manual API-mode run completed with a live key through `WCA_ENV_FILE=.env.example`; `targeted_backfill_summary.json` reports `status: ok`
- probe runs can target a copied local `outputs\research_local.db` during validation setup
- failed runs still emit `targeted_backfill_summary.json` with `status: error` and `failed_step`
- live crawler-mode run completed with:
  - a workstation-local external crawler scripts directory
  - a workstation-local Python runtime with Crawl4AI installed
  - result file `outputs\release_crawler_probe_v4\targeted_backfill_summary.json`

Not validated in the current workspace:

- host install clicks in Codex or Claude Code UI, because only manifest-level local validation is available here
- zero-config crawler install flow, because the validated crawler run also required an explicit Crawl4AI Python runtime override
- full Codex plugin install activation, because the CLI only exposed marketplace registration in this workspace

Observed probe failures:

- API probe hit the live odds endpoint and failed with `the_odds_api_http_401`; the provider body reported `INVALID_KEY` for the placeholder key.
- The later successful API run still recorded `odds.diagnostics[0].status = no_matching_odds_event_available`, so the command path is validated even though live odds coverage for that fixture remained unavailable.
- Crawler probe advanced into the collector import path and failed on missing external module `whoscored_workflow`.
- Crawler closed loop later succeeded with an explicit crawler Python override; the remaining runtime limitation in that run was `no_matching_odds_event_available`, which left readiness blocked on odds and lineup coverage rather than command execution.

Expected host install caveat:

- both marketplace manifests use `./` as the plugin source, and this release layout only works if the host resolves that path to the checked-out repo root rather than to the marketplace file directory
- Codex CLI did successfully register the repo as a local marketplace, but it did not expose a separate local plugin-install command in this workspace

## Go / no-go

Current recommendation: not ready to tag `v0.1.0` yet.

Tag `v0.1.0` only after one operator runs:

- one host-side install check in Codex
- one host-side install check in Claude Code
