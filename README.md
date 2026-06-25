# World Cup Agent Open

[English](README.md) | [简体中文](README.zh-CN.md)

Open-source World Cup match prediction plugin for Codex and Claude Code.

It pulls prematch facts and odds from your own providers, converts them into a structured feature vector, and returns machine-readable match predictions with probabilities, risk, coverage, expected goals, Poisson scorelines, totals, and BTTS when inputs are available.

- no frontend
- no hosted backend
- no push delivery
- no betting execution

## What this plugin does

- predicts World Cup fixtures with a local algorithmic pipeline
- supports independent research and odds providers
- derives scorelines, over/under 2.5, and BTTS through a Poisson scoreline model when attack/defense inputs are available
- returns stable JSON that an agent can explain in natural language
- runs with a keyless offline demo before you configure live APIs
- ships Codex and Claude Code plugin manifests from the repo root

The open package does not guarantee betting profitability and must not be treated as financial advice.

## Model overview

The current open-source prediction path is a local, deterministic pipeline:

1. collect recent-results, player-form, lineup, schedule, motivation, and odds inputs
2. build a prematch feature vector for the target fixture
3. score 8 dimensions: `team_strength`, `recent_form`, `attack_defense_efficiency`, `schedule_fatigue`, `key_player_status`, `odds_movement`, `lineup_integrity`, `motivation_stage`
4. route 1X2 probabilities through the weighted scoring model
5. infer home/away expected goals from attack and defense rates
6. use an independent Poisson scoreline model to derive likely scorelines, over/under 2.5, and BTTS probabilities when the required inputs are available
7. when enough official World Cup review samples exist, apply Bayesian calibration to baseline probabilities

Current implementation references:

- weighted prematch scoring and routing: [src/scoring/pre_match_research_preview.py](src/scoring/pre_match_research_preview.py)
- expected-goals inference: [src/scoring/expected_goals.py](src/scoring/expected_goals.py)
- Poisson scoreline model: [src/scoring/scoreline_model.py](src/scoring/scoreline_model.py)
- Bayesian calibration: [src/scoring/bayesian_calibration.py](src/scoring/bayesian_calibration.py)
- orchestration and persistence: [app/research_db/pre_match_research_scoring.py](app/research_db/pre_match_research_scoring.py)

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

The installed demo command is:

```bash
world-cup-agent-demo
```

The installed prediction command is:

```bash
world-cup-predict --local-date 2026-06-13
```

## After installation

In the chat window, a minimal prompt is enough:

```text
帮我预测明天的世界杯比赛
```

English equivalent:

```text
Predict tomorrow's World Cup matches
```

If you want one fixture only, include the date, teams, or fixture id.

## Live API configuration

Copy the sample configuration first:

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
SPORTRADAR_SOCCER_API_KEY=
THE_ODDS_API_KEY=
```

Supported provider values:

- research: `auto`, `sportradar_soccer`, `crawler`, `skip`
- odds: `auto`, `the_odds_api`, `crawler`, `skip`

### API examples

Paid or trial research facts from Sportradar:

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<your-key>
```

Odds from The Odds API:

```dotenv
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<your-key>
THE_ODDS_API_SPORT_KEY=soccer_fifa_world_cup
```

Hybrid setup:

```dotenv
DEFAULT_RESEARCH_PROVIDER=sportradar_soccer
SPORTRADAR_SOCCER_API_KEY=<your-key>
DEFAULT_ODDS_PROVIDER=the_odds_api
THE_ODDS_API_KEY=<your-key>
```

### Official signup links

Verified on June 25, 2026:

- Sportradar Sports Data API free trial: [sportradar.com/media-tech/data-content/sports-data-api](https://sportradar.com/media-tech/data-content/sports-data-api/)
- Sportradar developer getting started: [developer.sportradar.com/getting-started/docs/get-started](https://developer.sportradar.com/getting-started/docs/get-started)
- Sportradar account setup guide: [developer.sportradar.com/football/docs/football-ig-account-setup](https://developer.sportradar.com/football/docs/football-ig-account-setup)
- The Odds API homepage and free starter plan: [the-odds-api.com](https://the-odds-api.com/)
- The Odds API v4 docs: [the-odds-api.com/liveapi/guides/v4](https://the-odds-api.com/liveapi/guides/v4/)

Source note:

- Sportradar documents a free trial flow through its developer portal.
- The Odds API homepage shows a free starter plan with 500 credits per month as of June 25, 2026.

## Optional crawler fallback

The crawler runtime is not bundled. If you want a self-hosted path, install a compatible crawler separately and then configure:

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

## Stable prediction output

The open package emits structured output that agents can explain directly. Key top-level fields include:

```json
{
  "schema_version": "world_cup_prediction.v1",
  "status": "ok | partial | failed",
  "source": {
    "research_provider": "sportradar_soccer | crawler | skip | existing_db",
    "odds_provider": "the_odds_api | crawler | skip | existing_db"
  },
  "predictions": [
    {
      "fixture_id": "fixture_wc2026_...",
      "match_time_beijing": "2026-06-13T08:00:00+08:00",
      "home_team": "Home",
      "away_team": "Away",
      "data_status": "ok | partial | failed",
      "probabilities": {
        "home_win": 0.0,
        "draw": 0.0,
        "away_win": 0.0,
        "over_2_5": 0.0,
        "under_2_5": 0.0,
        "btts_yes": 0.0,
        "btts_no": 0.0
      },
      "expected_goals": {
        "home_expected_goals": 0.0,
        "away_expected_goals": 0.0
      },
      "scoreline_model": {
        "family": "independent_poisson"
      },
      "prediction_routing": {},
      "recommended_scores": ["1:1", "1:0", "2:1"],
      "risk": {
        "level": "low | medium | high",
        "confidence": 0
      },
      "coverage": {},
      "calibration": {},
      "gaps": []
    }
  ]
}
```

BTTS, scorelines, and over/under probabilities come from the Poisson scoreline route when attack/defense inputs are available. Capital allocation and risk/reward are reported only when the script returns them. The open model must not invent missing values.

## Plugin usage

The repository root is the plugin root.

- Codex plugin manifest: `.codex-plugin/plugin.json`
- Codex repo marketplace: `.agents/plugins/marketplace.json`
- Claude Code plugin manifest: `.claude-plugin/plugin.json`
- Claude Code repo marketplace: `.claude-plugin/marketplace.json`
- prediction skill: `skills/world-cup-prediction/`
- auxiliary data-repair skill: `skills/world-cup-research-backfill/`

See [PLUGIN_USAGE.md](PLUGIN_USAGE.md) for host installation and verification steps.

## More docs

- [README.zh-CN.md](README.zh-CN.md): Chinese version
- [ARCHITECTURE.md](ARCHITECTURE.md): provider routing, data flow, and scoring boundaries
- [SECURITY.md](SECURITY.md): secrets, crawler trust boundary, and safe operation
- [PLUGIN_USAGE.md](PLUGIN_USAGE.md): install and verify in Codex or Claude Code

## Validation

```bash
python -m compileall app src scripts tests
python -m pytest -q
python scripts/run_demo.py
python -m build
```

## License

MIT. See [LICENSE](LICENSE).
