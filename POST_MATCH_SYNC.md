# Post-Match Sync

World Cup Agent Open can persist completed-match results, mapped player appearances, and formal pre-match prediction snapshots in the local Research DB.

The public package provides a manual CLI. It does not include a background scheduler, bot delivery, API server, or private post-match learning store.

## Closed loop provided by the open core

```text
formal prediction
  -> pre_match_predictions
completed fixture
  -> match_results
  -> player_match_appearances
next fixture
  -> latest eligible appearance feeds the key-player last-match term
```

The scoring model remains:

```text
recent aggregate 70% + last-match status 30%
```

Real starter/minutes data takes precedence over the existing recent-window proxy. The proxy remains available when real appearance data is missing.

## Additive tables

`ResearchDatabaseRepository.initialize()` creates two additive tables:

- `pre_match_predictions`: exact validated prediction JSON, version/weights metadata, generation time, and optional feature snapshot link;
- `player_match_appearances`: fixture/player/team identity, appeared, starter, minutes, position, shirt number, source IDs, and availability time.

Existing tables are not rewritten. Missing player minutes remain `NULL` and produce partial quality rather than being converted to zero.

## Provider requirements

The bundled Sportradar adapter exposes:

- sport-event summary;
- official lineups;
- extended sport-event summary when the extended endpoint is enabled.

Configuration:

```dotenv
SPORTRADAR_SOCCER_API_KEY=<key>
SPORTRADAR_SOCCER_EXTENDED_ENABLED=true
SPORTRADAR_SOCCER_EXTENDED_BASE_URL=https://api.sportradar.com/soccer-extended
POST_MATCH_SYNC_PROVIDER=sportradar_soccer
```

Extended access is provider-plan dependent. A valid final result with lineup data but no minutes is reported as `partial`.

## CLI

After installation:

```bash
world-cup-post-match-sync --help
```

Dry-run recent completed fixtures:

```bash
world-cup-post-match-sync \
  --db-path outputs/research_local.db \
  --lookback-hours 48 \
  --dry-run
```

Target an explicit fixture:

```bash
world-cup-post-match-sync \
  --db-path outputs/research_local.db \
  --fixture-id fixture_wc2026_66456916
```

Options:

- `--fixture-id`: repeatable explicit fixture target;
- `--lookback-hours`: candidate lookback window;
- `--delay-minutes`: conservative delay before candidate selection;
- `--max-fixtures`: batch cap;
- `--dry-run`: no database mutation;
- `--force`: fetch even when result and appearances are already present;
- `--output`: optional summary JSON path.

## Summary quality

Each fixture reports:

```json
{
  "status": "ok | partial | failed | skipped",
  "data_quality": {
    "result": "ok | missing",
    "lineup": "ok | partial | missing",
    "player_appearances": "ok | partial | missing",
    "prediction_snapshot": "ok | missing",
    "post_match_review": "ok | skipped | failed"
  }
}
```

In the open package, `post_match_review` is normally `skipped` because the private learning/review runtime is intentionally excluded.

## Cutoff safety

The next prediction can use an appearance only when:

```text
appearance.played_at < target fixture match_time
appearance.available_at <= feature available_at_cutoff
```

This prevents future or late-arriving facts from entering a historical pre-match prediction.

## Security

- API keys are read from environment configuration only.
- Raw provider payloads are not persisted by default.
- Unmapped players are diagnostics; the system never assigns one player's minutes to another player.
- Run the first live sync against a copied database and use `--dry-run` before writes.
