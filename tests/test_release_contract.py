import csv
import json
import subprocess
import sys
from pathlib import Path

from app import __version__
from app.config import load_settings
from app.research_db.world_cup_research_backfill import (
    _build_data_quality,
    _load_public_backfill_data,
    _overall_backfill_status,
)


ROOT = Path(__file__).resolve().parents[1]


def test_public_backfill_contract_uses_arrays_and_fixed_quality_values(tmp_path) -> None:
    with (tmp_path / "national_recent_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["fixture_id", "home_team_id", "away_team_id", "opponent_team_id"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "fixture_id": "recent-1",
                "home_team_id": "team-a",
                "away_team_id": "team-c",
                "opponent_team_id": "team-c",
            }
        )
    (tmp_path / "player_form_snapshots.json").write_text(
        json.dumps([{"player_id": "player-1", "team_id": "team-a"}]),
        encoding="utf-8",
    )
    (tmp_path / "odds_snapshots.json").write_text("[]", encoding="utf-8")

    data = _load_public_backfill_data(
        tmp_path,
        target_fixture_ids={"fixture-1"},
        target_team_ids={"team-a", "team-b"},
    )
    quality = _build_data_quality(
        recent_results={"status": "ok"},
        player_form={"status": "partial"},
        odds={"status": "skipped", "reason": "odds_provider_unavailable"},
        data=data,
    )

    assert all(isinstance(value, list) for value in data.values())
    assert quality == {
        "recent_results": "ok",
        "player_form": "partial",
        "odds": "missing",
    }
    assert _overall_backfill_status(quality) == "partial"
    assert set(quality.values()) <= {"ok", "partial", "missing"}


def test_offline_demo_is_keyless_deterministic_and_machine_readable() -> None:
    command = [sys.executable, "scripts/run_demo.py", "--compact"]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout

    payload = json.loads(first.stdout)
    assert payload["status"] == "ok"
    assert payload["match_summary"]["fixture_id"]
    assert set(payload["data_quality"]) == {"recent_results", "player_form", "odds"}
    assert all(payload["data_quality"][key] == "ok" for key in payload["data_quality"])
    assert all(isinstance(payload["data"][key], list) for key in payload["data_quality"])
    assert payload["data"]["player_form"]
    assert payload["data"]["odds"]
    assert payload["source"]["research_provider"] == "offline_demo_fixture"


def test_v1_release_metadata_and_required_docs_are_present() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    codex_plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude_marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))

    assert 'version = "1.0.0"' in pyproject
    assert __version__ == "1.0.0"
    assert codex_plugin["version"] == "1.0.0"
    assert claude_plugin["version"] == "1.0.0"
    assert claude_marketplace["plugins"][0]["version"] == "1.0.0"
    for relative_path in (
        "LICENSE",
        "SECURITY.md",
        "ARCHITECTURE.md",
        "PLUGIN_USAGE.md",
        "release-notes-v1.0.0.md",
        "CHANGELOG.md",
        "schemas/targeted_backfill_summary.schema.json",
    ):
        assert (ROOT / relative_path).is_file(), relative_path


def test_dotenv_can_configure_independent_providers_and_crawler_path(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    scripts_dir = tmp_path / "crawler-scripts"
    env_file.write_text(
        "\n".join(
            [
                "DEFAULT_RESEARCH_PROVIDER=crawler",
                "DEFAULT_ODDS_PROVIDER=the_odds_api",
                "ENABLE_CRAWLER=false",
                f"SPORTS_STABLE_CRAWL_SCRIPTS_DIR={scripts_dir}",
            ]
        ),
        encoding="utf-8",
    )
    for key in (
        "DEFAULT_RESEARCH_PROVIDER",
        "DEFAULT_ODDS_PROVIDER",
        "ENABLE_CRAWLER",
        "SPORTS_STABLE_CRAWL_SCRIPTS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WCA_ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.data_source_research_provider == "crawler"
    assert settings.data_source_odds_provider == "the_odds_api"
    assert settings.enable_crawler is False
    assert settings.sports_stable_crawl_scripts_dir == scripts_dir


def test_env_example_exposes_independent_provider_configuration() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "DEFAULT_RESEARCH_PROVIDER=",
        "DEFAULT_ODDS_PROVIDER=",
        "ENABLE_CRAWLER=",
        "SPORTRADAR_SOCCER_API_KEY=",
        "THE_ODDS_API_KEY=",
        "SPORTS_STABLE_CRAWL_SCRIPTS_DIR=",
    ):
        assert key in env_example
    assert "source-mode" not in env_example.lower()
