from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


class ValidationError(ValueError):
    """Raised when local schema validation fails."""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_keys(obj: dict[str, Any], keys: list[str], label: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ValidationError(f"{label} missing required keys: {', '.join(missing)}")


def validate_match_input(match: dict[str, Any]) -> None:
    schema = load_json(ROOT / "schemas" / "match_input.schema.json")
    require_keys(match, schema["required"], "match input")
    if not isinstance(match["neutral_field"], bool):
        raise ValidationError("neutral_field must be boolean")
    if not isinstance(match["odds_snapshots"], list):
        raise ValidationError("odds_snapshots must be a list")
    team_features = match["team_features"]
    require_keys(team_features, ["home", "away"], "team_features")
    if not isinstance(team_features["home"], dict) or not isinstance(team_features["away"], dict):
        raise ValidationError("team_features.home and team_features.away must be objects")
    for snapshot in match["odds_snapshots"]:
        require_keys(snapshot, ["snapshot_time", "bookmaker"], "odds snapshot")


def validate_analysis_output(output: dict[str, Any]) -> None:
    schema = load_json(ROOT / "schemas" / "analysis_output.schema.json")
    require_keys(output, schema["required"], "analysis output")
    probabilities = output["probabilities"]
    require_keys(probabilities, ["home_win", "draw", "away_win", "over_2_5", "upset_risk"], "probabilities")
    total = probabilities["home_win"] + probabilities["draw"] + probabilities["away_win"]
    if abs(total - 1.0) > 0.02:
        raise ValidationError(f"1x2 probabilities must sum to 1.0, got {total:.4f}")
    if output["risk_level"] not in {"low", "medium", "high"}:
        raise ValidationError("risk_level must be low, medium, or high")
    if not 0 <= output["confidence"] <= 100:
        raise ValidationError("confidence must be in 0-100")
    if "不构成收益承诺" not in output["disclaimer"]:
        raise ValidationError("disclaimer must contain no-profit-guarantee statement")
