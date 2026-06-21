# Release Notes: v1.0.0

## Summary

Version 1.0.0 is the first release-ready provider-based plugin package.

Research facts and odds are independent capabilities:

- Sportradar Soccer or an optional crawler can provide research facts.
- The Odds API or an optional crawler can provide odds.
- Paid, self-hosted, and hybrid combinations can be configured independently.

## Added

- `BaseProvider`, `ProviderResult`, and concrete provider contracts
- central `provider_router` with capability-specific availability checks
- explicit crawler fallback diagnostics
- stable targeted-backfill output contract and JSON Schema
- deterministic keyless demo through `python scripts/run_demo.py`
- provider, router, fallback, schema, failure, demo, and manifest tests
- architecture, security, plugin usage, and release documentation

## Changed

- package and plugin versions are `1.0.0`
- `httpx` is declared as a runtime dependency
- crawler scripts configuration is loaded through application settings
- `DEFAULT_RESEARCH_PROVIDER`, `DEFAULT_ODDS_PROVIDER`, and `ENABLE_CRAWLER` are the primary settings
- legacy `DATA_SOURCE_RESEARCH_PROVIDER` and `DATA_SOURCE_ODDS_PROVIDER` remain compatible aliases
- Sportradar batch collection uses the unified provider contract

## Public output contract

Every generated `targeted_backfill_summary.json` includes:

- `status`: `ok`, `partial`, or `failed`
- `data_quality` for recent results, player form, and odds
- array-valued `data` for all three domains
- independent research and odds source metadata

## Compatibility notes

- Failed runs now use `status: failed` instead of `status: error`.
- `--source-mode` is deprecated compatibility metadata and does not select providers.
- Provider selection comes from `DEFAULT_RESEARCH_PROVIDER` and `DEFAULT_ODDS_PROVIDER`.

## Known limitations

- Sportradar player form depends on local-name-to-provider-player mapping. Unmatched players produce explicit partial or missing data.
- Live providers can omit events, rate-limit, or change schemas.
- A targeted live backfill requires a seeded research database containing the target fixtures.
- Host installation must be verified after publishing from a clean clone.

## Release gate

Tag and submit v1.0.0 only after:

1. clean-clone package, test, and demo validation
2. one Codex host installation check
3. one Claude Code host installation check
4. one target-fixture probe for each live provider path claimed as supported
