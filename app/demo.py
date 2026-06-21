from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEMO_FIXTURE_ID = "demo_argentina_france"
DEMO_AS_OF = "2026-06-20T12:00:00+00:00"


def build_demo_payload() -> dict[str, Any]:
    """Build a deterministic, keyless plugin demo payload."""

    recent_results = [
        {
            "fixture_id": "demo_recent_arg_1",
            "team_id": "team_argentina",
            "opponent": "Brazil",
            "result": "win",
            "goals_for": 2,
            "goals_against": 1,
        },
        {
            "fixture_id": "demo_recent_arg_2",
            "team_id": "team_argentina",
            "opponent": "Uruguay",
            "result": "draw",
            "goals_for": 1,
            "goals_against": 1,
        },
        {
            "fixture_id": "demo_recent_fra_1",
            "team_id": "team_france",
            "opponent": "Germany",
            "result": "win",
            "goals_for": 3,
            "goals_against": 1,
        },
        {
            "fixture_id": "demo_recent_fra_2",
            "team_id": "team_france",
            "opponent": "Spain",
            "result": "loss",
            "goals_for": 0,
            "goals_against": 1,
        },
    ]
    player_form = [
        {
            "player_id": "demo_player_arg_10",
            "team_id": "team_argentina",
            "player_name": "Argentina No. 10",
            "recent_matches": 5,
            "recent_minutes": 421,
            "recent_goals": 4,
            "recent_assists": 2,
        },
        {
            "player_id": "demo_player_fra_10",
            "team_id": "team_france",
            "player_name": "France No. 10",
            "recent_matches": 5,
            "recent_minutes": 438,
            "recent_goals": 3,
            "recent_assists": 3,
        },
    ]
    odds = [
        {
            "fixture_id": DEMO_FIXTURE_ID,
            "market_type": "h2h",
            "home_decimal": 2.55,
            "draw_decimal": 3.20,
            "away_decimal": 2.85,
            "captured_at": DEMO_AS_OF,
            "source": "offline_demo_fixture",
        }
    ]

    points = {"win": 3, "draw": 1, "loss": 0}
    recent_ppg: dict[str, float] = {}
    for team_id in ("team_argentina", "team_france"):
        team_rows = [row for row in recent_results if row["team_id"] == team_id]
        recent_ppg[team_id] = round(
            sum(points[row["result"]] for row in team_rows) / len(team_rows),
            3,
        )

    h2h = odds[0]
    raw_probabilities = {
        "home": 1 / h2h["home_decimal"],
        "draw": 1 / h2h["draw_decimal"],
        "away": 1 / h2h["away_decimal"],
    }
    overround = sum(raw_probabilities.values())
    implied_probabilities = {
        outcome: round(probability / overround, 4)
        for outcome, probability in raw_probabilities.items()
    }

    return {
        "status": "ok",
        "match_summary": {
            "fixture_id": DEMO_FIXTURE_ID,
            "competition": "FIFA World Cup demo",
            "home_team": "Argentina",
            "away_team": "France",
            "as_of": DEMO_AS_OF,
            "recent_points_per_game": {
                "home": recent_ppg["team_argentina"],
                "away": recent_ppg["team_france"],
            },
            "market_implied_probabilities": implied_probabilities,
            "note": "Offline illustrative data; not a live prediction or betting recommendation.",
        },
        "data_quality": {
            "recent_results": "ok",
            "player_form": "ok",
            "odds": "ok",
        },
        "data": {
            "recent_results": recent_results,
            "player_form": player_form,
            "odds": odds,
        },
        "source": {
            "research_provider": "offline_demo_fixture",
            "odds_provider": "offline_demo_fixture",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the keyless World Cup Agent offline demo.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    args = parser.parse_args()

    payload = build_demo_payload()
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
