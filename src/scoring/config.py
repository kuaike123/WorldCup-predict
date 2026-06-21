from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "configs" / "weights_v0_5.yaml"


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip('"').strip("'")


def load_weights(path: Path | None = None) -> dict[str, Any]:
    """Read the limited YAML structure used by v0.5 without extra dependencies."""
    config_path = path or DEFAULT_CONFIG_PATH
    result: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw_line.startswith(" "):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = _parse_scalar(value)
                current_section = None
            else:
                result[key] = {}
                current_section = key
            continue
        if current_section is None:
            raise ValueError(f"Unexpected nested config line: {raw_line}")
        key, value = line.strip().split(":", 1)
        result[current_section][key.strip()] = _parse_scalar(value)

    weights = result.get("pre_match_weights", {})
    total = round(sum(float(v) for v in weights.values()), 6)
    expected = float(result.get("constraints", {}).get("total_weight", 1.0))
    if total != round(expected, 6):
        raise ValueError(f"pre_match_weights sum is {total}, expected {expected}")
    if result.get("constraints", {}).get("auto_apply_weight_adjustment") is not False:
        raise ValueError("auto_apply_weight_adjustment must be false")
    return result
