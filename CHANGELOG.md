# Changelog

All notable changes to this project are documented here.

## [1.0.0] - 2026-06-20

### Added

- independent research and odds provider router
- public provider contracts for Sportradar Soccer, The Odds API, and optional crawler integrations
- stable targeted-backfill summary schema with explicit data quality
- deterministic keyless demo and installed demo command
- provider, fallback, schema, failure, demo, and release metadata tests
- architecture, security, plugin usage, source policy, and v1 release documentation

### Changed

- provider selection now uses `DEFAULT_RESEARCH_PROVIDER` and `DEFAULT_ODDS_PROVIDER`
- legacy `--source-mode` is compatibility metadata only
- failed summaries use `status: failed`
- Sportradar batch collection calls the provider contract
- crawler scripts directory can be supplied through `.env`
- runtime and development dependencies are declared in `pyproject.toml`

### Known limitations

- real Sportradar player-form coverage depends on accurate player identity mapping
- live provider and marketplace-host validation remains environment-dependent

## [0.1.0] - 2026-06-19

### Added

- initial trimmed public repository
- targeted backfill and scoring core
- local Codex and Claude Code plugin manifests
- initial Sportradar research adapter and split provider configuration
