# Post-Match Data Feedback Loop Spec v1.0

**Status:** implemented and locally validated / pending live-provider and host validation  
**Primary repository:** `world-cup-agent`  
**Public sync target:** `world-cup-agent-open`  
**Target outcome:** close the loop from pre-match prediction to verified post-match facts, then feed the latest real match and player-appearance data into the next prediction.

---

## 1. Objective

Build a minimum viable, idempotent post-match feedback loop with four durable outputs:

1. completed fixture results are automatically written to Research DB `match_results`;
2. per-fixture player appearances are stored with at least starter status and minutes for mapped key players;
3. the exact pre-match prediction JSON used before kickoff is persisted in Research DB;
4. the next fixture's key-player score prefers the latest real appearance and falls back to the existing recent-window proxy only when real appearance data is unavailable.

The implementation must preserve the current scoring model's 70/30 blend:

```text
key_player_form_score = recent_window_aggregate * 0.70
                      + last_match_status * 0.30
```

The change is primarily a data-closure project, not an odds-weight retuning project.

---

## 2. Confirmed Current State

### 2.1 Existing algorithm in `world-cup-agent`

The private repository already contains a minimal last-match status proxy in:

- `app/research_db/pre_match_research_features.py`
- `src/scoring/pre_match_research_preview.py`
- `tests/test_pre_match_research_feature_builder.py`

Current behavior:

- base recent-form score uses existing club/national aggregate windows;
- a proxy last-match status is inferred from aggregate matches, starts, and minutes;
- the final blend is 70% aggregate and 30% proxy;
- no database schema was changed for this algorithm increment.

### 2.2 Open repository drift

`world-cup-agent-open` still has the earlier `_player_form_score` implementation without:

- `_last_match_key_player_status_score`;
- `_recent_window_status_score`;
- the 70/30 blend;
- the corresponding focused tests.

### 2.3 Result ingestion

The private repository has:

- a `match_results` table;
- `world_cup_2026_completed_match_ingest.py`;
- post-match learning packages and reviews in JSON store.

However, the existing completed-match ingest is a batch/manual path based on a dated static JSON file. It is not a scheduled provider-backed sync, and it does not persist per-match player appearances.

### 2.4 Prediction persistence

The current scoring service writes default predictions to:

```text
outputs/p0_15_pre_match_predictions.json
```

Other execution paths also place prediction-related data inside local JSON stores or post-match packages. Research DB has no canonical `pre_match_predictions` table. As a result, a post-match review can recompute or reconstruct a prediction instead of always evaluating the exact pre-kickoff snapshot.

### 2.5 Source-boundary conflict

The private repository currently defines Sportradar Soccer as live-only and prohibits post-match primary research. The required loop needs structured final scores, official starters, and player minutes. This spec therefore includes an explicit source-policy change:

- Sportradar may provide bounded **post-match factual data**;
- it must not provide editorial narrative, betting advice, or scoring-weight changes;
- crawler/manual sources remain fallback evidence, not silent substitutes for missing structured fields.

---

## 3. Scope

## 3.1 Scope In

### Private repository: `world-cup-agent`

- add durable tables for player appearances and pre-match predictions;
- extend repository read/write APIs and audit records;
- add provider-backed completed-match discovery and ingestion;
- persist result, starter, and minutes facts idempotently;
- persist exact pre-match prediction JSON from formal prediction paths;
- update post-match learning to use persisted prediction snapshots first;
- make next-match player-form features prefer the latest real appearance;
- add scheduler integration and a manual CLI;
- update source-boundary policy and tests;
- preserve existing JSON artifacts for compatibility.

### Public repository: `world-cup-agent-open`

- sync the already-completed 70/30 player-status algorithm change;
- add the database tables and repository methods that are part of the reusable core;
- add a provider/CLI-driven manual post-match sync, without private scheduler, bot, Feishu, API-server, or audience logic;
- add public documentation, deterministic fixtures, and tests;
- preserve independent research/odds provider architecture.

## 3.2 Scope Out

- changing the eight scoring-component weights;
- changing 1X2/over-under probability formulas;
- automated betting or wagering execution;
- player tracking beyond mapped football appearances;
- storing raw provider payloads by default;
- scraping unsupported sites to fabricate missing minutes;
- historical backfill of every competition or every player;
- replacing existing post-match review/calibration logic;
- copying private server, scheduler, bot, Feishu, Telegram, or entitlement modules into the open repository.

---

## 4. Minimum Viable Closed Loop

The minimum acceptable end-to-end flow is:

```text
T-3h or later pre-match generation
  -> build feature snapshot
  -> build prediction
  -> persist exact prediction JSON in Research DB
  -> optional JSON export remains available

match becomes closed
  -> discover recently completed fixture
  -> fetch official/structured summary and lineups/player statistics
  -> upsert match_results
  -> upsert mapped player_match_appearances
  -> create/reuse post-match learning package from persisted prediction
  -> create/reuse post-match review

next fixture for same team/player
  -> feature builder queries latest eligible player appearance before cutoff
  -> actual last-match starter/minutes score used at 30%
  -> aggregate recent-form score remains at 70%
  -> if actual appearance is missing, existing recent-window proxy is used
```

A run that only writes the final score but does not write player appearances must report `partial`, not `ok`.

---

## 5. Data Model

## 5.1 Existing `match_results`

Keep the existing schema unchanged. Continue using:

- `result_id`;
- `fixture_id`;
- final home/away scores;
- `result_status=closed`;
- `played_at`;
- `available_at`;
- source identity and timestamps.

No duplicate result row may be created for the same provider result identity.

## 5.2 New table: `player_match_appearances`

Add a non-destructive `CREATE TABLE IF NOT EXISTS` table:

```sql
CREATE TABLE IF NOT EXISTS player_match_appearances (
    appearance_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    appeared INTEGER NOT NULL CHECK(appeared IN (0, 1)),
    starter INTEGER CHECK(starter IN (0, 1)),
    minutes_played INTEGER,
    position TEXT,
    shirt_number INTEGER,
    source TEXT NOT NULL,
    source_appearance_id TEXT NOT NULL,
    source_fixture_id TEXT,
    source_player_id TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_appearance_id)
);
```

Required indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_player_match_appearances_player_played
ON player_match_appearances(player_id, played_at DESC);

CREATE INDEX IF NOT EXISTS idx_player_match_appearances_fixture
ON player_match_appearances(fixture_id, team_id);
```

Field rules:

- `appeared=0` is allowed only when the provider explicitly lists a squad/bench player with zero minutes;
- `starter` may be null when lineup data is unavailable;
- `minutes_played` may be null when the source does not expose it;
- do not convert missing minutes to zero unless the provider explicitly confirms zero minutes;
- `available_at` is when the post-match fact became available, not kickoff time;
- player/team/fixture IDs must map to existing local canonical IDs before persistence.

## 5.3 New table: `pre_match_predictions`

Add a durable prediction snapshot table:

```sql
CREATE TABLE IF NOT EXISTS pre_match_predictions (
    prediction_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    version TEXT NOT NULL,
    weights_version TEXT NOT NULL,
    feature_snapshot_id TEXT,
    status TEXT NOT NULL,
    prediction_json TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(fixture_id, generated_at, weights_version)
);
```

Required index:

```sql
CREATE INDEX IF NOT EXISTS idx_pre_match_predictions_fixture_generated
ON pre_match_predictions(fixture_id, generated_at DESC);
```

Persistence rules:

- `prediction_json` stores the exact validated prediction payload;
- JSON is the canonical snapshot payload; do not reconstruct it from flattened columns;
- `generated_at` and `as_of` must come from the prediction itself;
- only validated predictions may be persisted;
- multiple snapshots per fixture are allowed;
- `latest_pre_match_prediction(fixture_id, before=match_time)` selects the latest snapshot generated no later than kickoff;
- rerunning the same formal generation is idempotent under the unique key.

## 5.4 Table grouping and status

Do not silently redefine the existing P0 initialization contract. Add a separate repository constant:

```python
POST_MATCH_LOOP_TABLES = (
    "player_match_appearances",
    "pre_match_predictions",
)
```

Repository `status()` and `initialize()` must expose:

```json
{
  "post_match_loop_tables_present": true
}
```

Existing databases upgrade by running the existing idempotent `initialize()` path. No destructive migration or column rewrite is allowed in v1.

---

## 6. Repository Contract

Add the following methods to `ResearchDatabaseRepository`:

```python
upsert_player_match_appearance(record) -> dict
list_player_match_appearances(
    *, fixture_id: str | None = None,
    player_id: str | None = None,
    team_id: str | None = None,
    available_at_cutoff: str | None = None,
) -> list[dict]
latest_player_match_appearance(
    player_id: str,
    *,
    before_played_at: str,
    available_at_cutoff: str,
) -> dict | None

save_pre_match_prediction(prediction, *, feature_snapshot_id: str | None = None) -> dict
latest_pre_match_prediction(
    fixture_id: str,
    *,
    before_generated_at: str | None = None,
) -> dict | None
list_pre_match_predictions(fixture_id: str | None = None) -> list[dict]
```

Extend `upsert_facts()` to accept `player_match_appearances` and include audit rows/counts.

Repository methods must:

- call `initialize()` before writes;
- use transactions;
- return decoded `prediction_json` as a nested `prediction` field;
- preserve source IDs and audit summaries;
- never log or persist provider credentials.

---

## 7. Post-Match Provider and Source Policy

## 7.1 Source-boundary update in the private repository

Update `app/data_sources/source_boundary.py` so Sportradar has:

```text
allowed phases: live, post_match
provides:
  - live_timeline
  - live_commentary
  - live_events
  - post_match_final_result
  - post_match_official_lineup
  - post_match_player_appearance
prohibited:
  - pre_match_primary_research
  - post_match_editorial_research
  - odds
  - scoring_weight_change
```

`PHASE_SOURCE_RULES["post_match"]` becomes:

```python
("sportradar_soccer", "crawler")
```

Crawler remains a bounded fallback/public-evidence source. It must not fabricate structured starter/minutes facts.

## 7.2 Provider precedence

For each completed fixture:

1. **Sportradar structured summary** for final status and score;
2. **Sportradar official lineup** for starter/bench identity;
3. **extended or standard event summary player statistics** for minutes;
4. crawler/manual result evidence only when the structured result source is unavailable;
5. existing static completed-results JSON remains an explicit import fallback, not the automatic primary path.

## 7.3 Data-quality semantics

Each fixture sync result must expose:

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

Rules:

- final score present but no mapped key-player appearance data => overall `partial`;
- lineup present but minutes absent => `player_appearances=partial`;
- unmapped provider player IDs are diagnostics, not silently dropped successes;
- prediction missing must not block result/appearance persistence, but review generation is skipped;
- provider/network failure for one fixture must not roll back other fixtures in the batch.

---

## 8. Completed-Match Sync Service

Create or refactor into a reusable service, preferably:

```text
app/research_db/post_match_sync.py
```

Public API:

```python
class PostMatchSyncService:
    def sync_recent_completed_matches(
        self,
        *,
        lookback_hours: int = 48,
        delay_minutes: int = 30,
        max_fixtures: int = 20,
        fixture_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        ...
```

Required behavior:

1. query local fixtures whose `match_time` is in the lookback window;
2. exclude fixtures already closed with complete appearance coverage unless `force` is set;
3. fetch provider summary only for candidate fixtures;
4. require provider event status to be closed/ended before writing a final result;
5. normalize final score and appearance rows;
6. map provider team/player IDs to canonical local IDs using source IDs first, aliases/names second;
7. persist result and mapped appearance rows transactionally per fixture;
8. create/reuse post-match learning package and review when an eligible persisted prediction exists;
9. return a deterministic batch summary;
10. support `dry_run` with zero database/store mutation.

The existing dated static-file sync may call this service through an import adapter or remain as a documented fallback. Duplicate orchestration logic must not be maintained in two independent implementations.

---

## 9. Player Appearance Normalization

Create focused normalizer functions with parser-level tests. Inputs may include:

- event summary;
- extended event summary;
- official lineup response;
- local players/squads.

Output per player:

```json
{
  "fixture_id": "fixture_wc2026_x",
  "player_id": "player_local_x",
  "team_id": "team_local_x",
  "played_at": "2026-06-21T...",
  "appeared": true,
  "starter": true,
  "minutes_played": 90,
  "position": "forward",
  "shirt_number": 10,
  "source": "sportradar_soccer",
  "source_appearance_id": "sr:sport_event:...:sr:player:...",
  "source_fixture_id": "sr:sport_event:...",
  "source_player_id": "sr:player:...",
  "available_at": "2026-06-21T..."
}
```

Mapping order:

1. local `players.source_player_id` exact match;
2. local alias/source mapping;
3. normalized exact player name within the fixture team;
4. otherwise emit an unmapped diagnostic and do not attach statistics to another player.

Minimum key-player coverage is measured against the locally selected/core players for the two fixture teams. A fixture may be `ok` only when all key players listed by the provider are either mapped or explicitly diagnosed as not in squad/did not appear.

---

## 10. Prediction Persistence

## 10.1 No implicit mutation in pure analysis helpers

Keep `analyze_research_feature_vector()` and low-level scoring functions pure.

Add explicit orchestration methods:

```python
build_and_save_prediction(fixture_id: str) -> dict
build_feature_prediction_and_save(fixture_id: str) -> tuple[dict, dict]
```

or an equivalent clearly named persistence boundary.

## 10.2 Paths that must persist

The following formal paths must save the validated prediction:

- default artifact generation;
- formal bot/report generation used for delivery;
- match-day watch prediction snapshots when they represent a newly generated formal prediction;
- targeted backfill readiness prediction generation when configured to generate formal predictions.

Ad-hoc preview/test paths may remain non-persistent when explicitly requested.

## 10.3 JSON compatibility

Continue writing `outputs/p0_15_pre_match_predictions.json` for compatibility. The database becomes the durable source for post-match comparison; JSON remains an export/artifact.

## 10.4 Post-match learning read order

`PostMatchLearningService._prediction(fixture_id)` must resolve:

1. latest persisted prediction generated no later than kickoff;
2. compatible prediction from existing JSON/store package;
3. recompute only as a last-resort fallback, with an explicit reason code such as `prediction_recomputed_after_match`.

A recomputed post-match prediction must never be presented as the original pre-match snapshot.

---

## 11. Feature-Builder Integration

## 11.1 Real appearance fields

When building player form for a future fixture, enrich each player row with:

```text
last_match_fixture_id
last_match_played_at
last_match_started
last_match_minutes
last_match_appearance_source
last_match_status_source = actual_appearance | recent_window_proxy | unavailable
```

These fields may be feature-vector fields; they do not need to be added to `player_form_snapshots` in v1.

## 11.2 Scoring precedence

Update `_last_match_key_player_status_score`:

1. if an eligible real appearance exists, score it from actual starter/minutes;
2. otherwise use the existing club aggregate proxy;
3. otherwise use the existing national aggregate proxy;
4. otherwise return `None` and retain the base aggregate score.

Actual appearance scoring:

```text
starter and minutes >= 75 -> 100
starter and minutes >= 60 -> 85
starter and minutes > 0   -> 65
not starter and minutes >= 30 -> 50
not starter and minutes > 0   -> 25
explicitly no appearance      -> 0
missing minutes/starter       -> partial; use only fields known
```

The final 70/30 blend remains unchanged.

## 11.3 Cutoff safety

The selected appearance must satisfy both:

```text
played_at < target fixture match_time
available_at <= feature available_at_cutoff
```

No same-day future or post-cutoff fact may enter a pre-match prediction.

## 11.4 Explainability

Update key-player component source fields to include:

```text
player_match_appearances.starter
player_match_appearances.minutes_played
player_match_appearances.played_at
```

Retain current aggregate source fields as documented fallbacks.

Add summary diagnostics:

```json
{
  "last_match_actual_used": 4,
  "last_match_proxy_used": 3,
  "last_match_unavailable": 1
}
```

---

## 12. Scheduler and CLI

## 12.1 Private scheduler

Add disabled-by-default settings:

```dotenv
POST_MATCH_SYNC_ENABLED=false
POST_MATCH_SYNC_INTERVAL_SECONDS=900
POST_MATCH_SYNC_LOOKBACK_HOURS=48
POST_MATCH_SYNC_DELAY_MINUTES=30
POST_MATCH_SYNC_MAX_FIXTURES=20
POST_MATCH_SYNC_PROVIDER=sportradar_soccer
```

Scheduler rules:

- run no more frequently than configured;
- delay at least 30 minutes after scheduled kickoff plus a conservative match-duration window, or rely on closed event status;
- process fixtures independently;
- log summary counts, never credentials/raw payloads;
- reruns are idempotent;
- scheduler failure must not stop pre-match jobs.

## 12.2 Manual CLI in both repositories

Add an installable/manual command:

```text
world-cup-post-match-sync
```

Required options:

```text
--db-path
--store-path             # private repo only; optional in open
--fixture-id             # repeatable
--lookback-hours
--delay-minutes
--max-fixtures
--dry-run
--force
--output                  # optional JSON summary path
```

Example:

```powershell
world-cup-post-match-sync `
  --db-path outputs\research_local.db `
  --lookback-hours 48 `
  --dry-run
```

---

## 13. Dual-Repository Synchronization Rules

## 13.1 Algorithm sync first

Before implementing the new loop in open, port the already-tested private algorithm delta exactly:

- `_player_form_score` 70/30 blend;
- `_last_match_key_player_status_score`;
- `_recent_window_status_score`;
- key-player source-field documentation;
- focused starter/substitute test cases.

Do not change numeric thresholds during the sync.

## 13.2 Shared-core parity

The following files/behaviors should remain functionally equivalent across repositories unless private-only dependencies require a wrapper:

```text
app/research_db/repository.py
app/research_db/pre_match_research_features.py
src/scoring/pre_match_research_preview.py
post-match result/appearance normalizers
prediction persistence repository methods
manual post-match sync core
```

## 13.3 Private-only modules

Do not copy these into open:

- API routes/server startup;
- scheduler wiring;
- bot delivery;
- Feishu/Telegram integrations;
- Free/VIP/Admin audience logic;
- private JSON-store workflows not required by the public core.

## 13.4 Drift test

Add a lightweight parity test or script that compares selected shared function source hashes/signatures, or maintain one canonical patch checklist. At minimum, tests in both repositories must lock identical output for the same player-form fixtures.

---

## 14. Implementation Phases

## Phase 0 — Baseline and safeguards

- inspect latest DB row dates/counts;
- capture current schema/table status;
- capture current prediction JSON behavior;
- copy the DB/store before live validation;
- add deterministic fixtures for completed match, lineup, and player statistics.

**Exit check:** baseline report proves no writes were made.

## Phase 1 — Sync existing algorithm to open

- port the 70/30 algorithm and source fields;
- add focused tests;
- run open pytest and Ruff.

**Exit check:** open produces the same expected `96.19` and `42.81` focused scores as private.

## Phase 2 — Database contract

- add two tables and indexes in both repositories;
- add repository methods and audit support;
- add idempotency/cutoff/query tests.

**Exit check:** existing DB initializes without data loss and repeated writes do not increase row counts.

## Phase 3 — Provider normalization

- update private source boundary;
- add completed fixture/result/lineup/minutes normalizers;
- add mapping diagnostics;
- extend the open provider contract for post-match facts without adding private runtime modules.

**Exit check:** deterministic provider fixtures yield one result and expected appearance rows.

## Phase 4 — Prediction persistence

- add explicit save methods;
- persist formal/default generation;
- retain JSON exports;
- change post-match learning read order.

**Exit check:** a post-match package uses byte-equivalent/structurally equivalent persisted pre-match prediction JSON and never silently substitutes a recomputed snapshot.

## Phase 5 — Sync orchestration

- implement `PostMatchSyncService`;
- refactor static completed-results path to reuse common persistence/orchestration;
- add CLI in both repos;
- add private scheduler wiring.

**Exit check:** dry-run makes no writes; live deterministic test writes result, appearances, and review exactly once.

## Phase 6 — Feature feedback

- query latest eligible appearances;
- prefer actual appearance in 30% status term;
- preserve proxy fallback;
- expose diagnostics/source fields.

**Exit check:** next-fixture prediction changes only in the key-player component when actual appearance differs from the proxy.

## Phase 7 — Documentation and release validation

- update architecture/source policy/env examples/README;
- document migration and rollback;
- run full test/lint/build suites in both repositories;
- perform one copied-DB live provider probe if credentials are available.

**Exit check:** all automated gates pass; unverified live coverage is explicitly documented.

---

## 15. Affected Modules

### Expected private-repository changes

```text
app/config.py
app/data_sources/source_boundary.py
app/data_sources/sportradar_soccer.py
app/research_db/repository.py
app/research_db/pre_match_research_features.py
app/research_db/pre_match_research_scoring.py
app/research_db/world_cup_2026_completed_match_ingest.py
app/research_db/post_match_sync.py                         # new
app/services/post_match_learning_service.py
app/services/match_day_watch_service.py                    # formal persistence hook only if required
app/services/bot_service.py                                # formal persistence hook only if required
app/scheduler/scheduler.py
src/scoring/pre_match_research_preview.py
scripts/run_post_match_sync.py                             # new or thin wrapper
pyproject.toml
.env.example
relevant tests and docs
```

### Expected open-repository changes

```text
app/config.py
app/research_db/repository.py
app/research_db/pre_match_research_features.py
app/research_db/pre_match_research_scoring.py
app/research_db/sportradar_soccer.py or provider contract extension
app/research_db/post_match_sync.py                         # new
src/scoring/pre_match_research_preview.py
scripts/run_post_match_sync.py                             # new
pyproject.toml
.env.example
README.md
ARCHITECTURE.md
SECURITY.md
PLUGIN_USAGE.md
relevant tests and schemas/docs
```

Actual implementation may use equivalent names, but it must preserve the boundaries in this spec.

---

## 16. Acceptance Criteria

### Data persistence

1. A closed fixture produces exactly one canonical `match_results` row per provider identity.
2. Mapped player appearances store real starter/minutes values when supplied by the provider.
3. Missing minutes remain null and cause partial quality; they are not coerced to zero.
4. Repeating the same sync is idempotent.
5. One fixture failure does not roll back successful fixture writes.

### Prediction snapshots

6. Every formal pre-match prediction path covered by this spec persists a validated JSON snapshot.
7. JSON artifact export remains available and compatible.
8. Post-match learning selects the latest persisted pre-kickoff prediction.
9. A recomputed fallback is explicitly labeled and never represented as the original prediction.

### Feature feedback

10. Actual prior appearance is used only when it passes match-time and availability cutoffs.
11. Actual starter/minutes take precedence over aggregate proxy fields.
12. Proxy behavior remains available when actual appearance data is absent.
13. The blend remains 70% aggregate and 30% last-match status.
14. No scoring-component weight or probability formula changes.

### Automation

15. Private scheduler is disabled by default and independently configurable.
16. Manual CLI supports dry-run and explicit fixture IDs.
17. Dry-run performs no DB or JSON-store mutation.
18. Summary output reports result, lineup, appearances, prediction, and review quality separately.

### Open-source parity

19. The private algorithm delta is present in open with matching tests.
20. Shared repository/data-contract behavior is covered in both repos.
21. No private scheduler/bot/delivery code is copied into open.
22. Open demo/tests require no real API key or network access.

### Safety and release

23. Existing databases upgrade via non-destructive table creation.
24. No API key or raw provider payload is written to DB, logs, fixtures, or summaries.
25. `pytest` and `ruff` pass in both repositories.
26. Open package build and console entry-point smoke checks pass.

---

## 17. Required Tests

## 17.1 Repository tests

- table creation on a fresh DB;
- upgrade against an existing DB with old tables/data;
- appearance upsert idempotency;
- prediction snapshot uniqueness and latest-before-kickoff selection;
- JSON round-trip fidelity;
- cutoff filtering.

## 17.2 Provider/normalizer tests

- closed event accepted;
- scheduled/live event skipped;
- final score parsing;
- starter parsing;
- minutes parsing;
- bench/no-appearance distinction;
- unmapped player diagnostics;
- partial response with lineup but no minutes;
- network/provider error classification.

## 17.3 Feature tests

- actual starter 90 minutes yields status score 100;
- actual substitute 20 minutes yields 25;
- explicit no appearance yields 0;
- missing actual row falls back to existing proxy;
- future/post-cutoff appearance excluded;
- 70/30 result locked numerically;
- open/private focused fixtures produce matching scores.

## 17.4 Prediction tests

- formal build persists once;
- default artifact build persists all successful predictions;
- invalid prediction rejected before persistence;
- post-match package reads persisted snapshot;
- recompute fallback reason is explicit.

## 17.5 Orchestration tests

- dry-run no mutation;
- one-fixture complete success;
- result-only partial;
- mixed batch with one provider failure;
- rerun idempotency;
- scheduler disabled by default;
- scheduler job isolation.

---

## 18. Validation Commands

Run in each applicable repository:

```powershell
python -m compileall app src scripts tests
python -m pytest -q
python -m ruff check app src scripts tests
```

Open repository additionally:

```powershell
python -m pip wheel . --no-deps --wheel-dir <temp-dir>
world-cup-post-match-sync --help
```

Manual copied-DB validation:

```powershell
world-cup-post-match-sync `
  --db-path <copied-research-db> `
  --store-path <copied-json-store> `
  --fixture-id <completed-fixture-id> `
  --dry-run

world-cup-post-match-sync `
  --db-path <copied-research-db> `
  --store-path <copied-json-store> `
  --fixture-id <completed-fixture-id>
```

Then verify:

```sql
SELECT * FROM match_results WHERE fixture_id = ?;
SELECT * FROM player_match_appearances WHERE fixture_id = ? ORDER BY team_id, player_id;
SELECT * FROM pre_match_predictions WHERE fixture_id = ? ORDER BY generated_at DESC;
```

Run a future fixture prediction and confirm its key-player diagnostics identify actual appearance usage.

---

## 19. Rollout and Backfill

1. deploy schema/repository code with scheduler disabled;
2. initialize a copied production DB and verify table creation;
3. run CLI dry-run for the last 48 hours;
4. run one explicit completed fixture;
5. compare final score, starters, and minutes with provider source;
6. rerun and verify idempotency;
7. backfill only completed 2026 World Cup fixtures after the current DB cutoff;
8. enable scheduler after at least one successful manual batch;
9. sync and validate the public repository;
10. retain old prediction JSON exports during the entire v1 rollout.

Backfill must be bounded by fixture IDs/date window and must not rewrite older verified rows unless `--force` is explicitly used.

---

## 20. Rollback

Code rollback:

- disable `POST_MATCH_SYNC_ENABLED`;
- stop using the new CLI/scheduler;
- revert feature-builder actual-appearance lookup while retaining proxy behavior.

Data rollback:

- new tables are additive and may remain unused;
- do not drop tables automatically;
- erroneous provider rows are removed only by explicit source/fixture-scoped maintenance commands;
- existing `match_results`, player-form snapshots, JSON predictions, and reviews remain intact.

---

## 21. Risks and Stop Conditions

### Known risks

- provider lineups may omit minutes;
- trial/plan API access may not include extended player statistics;
- provider player IDs may not match locally imported players;
- a prediction may be generated multiple times before kickoff;
- post-match facts can arrive later than expected;
- old JSON-store reviews may not identify the exact original prediction.

### Stop and request a decision when

- implementation requires altering or deleting existing result/player-form columns;
- the provider plan cannot supply either lineup or minutes for a representative completed fixture;
- player mapping would require fuzzy cross-team matching;
- formal prediction paths cannot be identified without changing public API semantics;
- source-policy expansion is rejected;
- open-source implementation would require copying private scheduler/bot code;
- a live validation would require using or exposing credentials not already configured by the operator.

---

## 22. Definition of Done

This mission is complete only when:

- the private repository automatically persists recent completed results and mapped key-player appearances;
- formal predictions are durably stored before matches;
- post-match reviews use persisted predictions;
- the next prediction uses real last-match starter/minutes data where available;
- fallback proxy behavior remains correct;
- the existing algorithm change is synchronized to open;
- both repositories pass required checks;
- one copied-DB end-to-end fixture proves prediction -> result/appearance -> review -> next-feature feedback;
- any unavailable live-provider coverage is documented as an unverified area rather than claimed as complete.
