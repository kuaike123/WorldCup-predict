# Security Policy

## Supported version

Security fixes are maintained for the current `1.0.x` release line.

## Reporting a vulnerability

Do not include secrets, exploit details, or personal data in a public issue.

Use the private security-advisory channel of the repository host after publication. If private advisories are unavailable, contact the maintainer through a private channel before opening a public issue. Include:

- affected version and platform
- minimal reproduction steps
- expected and observed behavior
- impact assessment
- whether credentials or user data may have been exposed

A public disclosure should wait until a fix or mitigation is available.

## Secrets

The plugin can use:

- `SPORTRADAR_SOCCER_API_KEY`
- `THE_ODDS_API_KEY`

Rules:

- store keys in environment variables or a local `.env` file
- never commit `.env`
- never place live keys in `.env.example`, tests, fixtures, logs, screenshots, issue reports, or generated summaries
- rotate a key immediately if it is exposed
- use provider-side quotas and key restrictions when available

The application does not intentionally serialize API keys into `targeted_backfill_summary.json`.

## Crawler trust boundary

The crawler path is optional and is not bundled with this release. `SPORTS_STABLE_CRAWL_SCRIPTS_DIR` points to executable Python code supplied by the operator.

Treat that code as trusted only after review:

- install it from a source you trust
- pin or record the reviewed revision
- run it in a dedicated virtual environment
- use least-privilege filesystem and network access
- do not run it as an administrator/root account
- verify target-site terms, robots policies, and applicable law
- expect browser automation and parsers to change or fail

The plugin checks for expected script names but does not sandbox or attest to third-party crawler code.

## Local file and database safety

- review `--db-path` and `--output-dir` before execution
- use a copy of important databases for first-time validation
- do not point output paths at unrelated directories
- generated output can contain fixture, team, player, and market data; handle it according to the source licenses and your own data policy
- v1.0 performs no database schema migration

## Network behavior

Paid-provider runs make outbound HTTPS requests to configured Sportradar and The Odds API base URLs. Crawler runs may make additional requests defined by the external crawler implementation.

Do not override provider base URLs with untrusted endpoints. An untrusted endpoint could capture API keys sent in headers or query parameters.

## Dependency safety

Before release or deployment:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
```

Review dependency updates, especially HTTP clients, parsers, and browser/crawler runtimes. The optional crawler environment has a larger attack surface than the core package.


## Post-match data safety

The post-match sync stores normalized final scores, player identity mappings, starter flags, and minutes. Raw provider payloads are not stored by default.

- Run the first live operation against a copied database with `--dry-run`.
- Treat missing minutes as unknown; do not replace them with zero.
- Review unmapped-player diagnostics instead of using fuzzy cross-team assignment.
- Do not enable or claim extended player-stat coverage unless the configured provider plan actually supplies it.
- The open package contains no automatic scheduler; operators control every sync invocation.

## Data and recommendation disclaimer

Odds and match-analysis outputs are informational. They may be delayed, incomplete, or wrong. The project does not execute wagers and should not be used as the sole basis for financial decisions.
