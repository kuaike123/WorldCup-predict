from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.storage.repository import LocalRepository
from app.storage.repository import utc_now
from src.scoring.bayesian_calibration import build_bayesian_calibration
from src.scoring.pre_match_research_preview import (
    P0_15_COMPONENT_DIMENSIONS,
    P0_15_VERSION,
    P0_15_WEIGHTS_VERSION,
    analyze_research_feature_vector,
    validate_pre_match_prediction,
)

from .pre_match_research_features import (
    DEFAULT_P0_11_BUNDLE_DIR,
    PreMatchResearchFeatureBuilder,
    PreMatchResearchFeatureError,
)
from .repository import ResearchDatabaseRepository


DEFAULT_P0_15_PREDICTIONS_PATH = ROOT / "outputs" / "p0_15_pre_match_predictions.json"
DEFAULT_P0_15_QUALITY_REPORT_PATH = (
    ROOT / "outputs" / "p0_15_pre_match_scoring_quality_report.json"
)
DEFAULT_P0_15_QUALITY_REPORT_MD_PATH = (
    ROOT / "outputs" / "p0_15_pre_match_scoring_quality_report.md"
)


class PreMatchResearchScoringService:
    def __init__(
        self,
        repository: ResearchDatabaseRepository,
        *,
        bundle_dir: Path = DEFAULT_P0_11_BUNDLE_DIR,
        expected_default_predictions: int = 12,
        local_store_repository: LocalRepository | None = None,
    ) -> None:
        self.repository = repository
        self.local_store_repository = local_store_repository
        self.builder = PreMatchResearchFeatureBuilder(
            repository,
            bundle_dir=bundle_dir,
            local_store_repository=local_store_repository,
        )
        self.expected_default_predictions = expected_default_predictions

    def build_prediction(self, fixture_id: str) -> dict[str, Any]:
        _feature_vector, prediction = self.build_prediction_with_feature_vector(fixture_id)
        return prediction

    def build_prediction_with_feature_vector(self, fixture_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        feature_vector = self.builder.build_feature_vector(fixture_id)
        prediction = analyze_research_feature_vector(feature_vector)
        validate_pre_match_prediction(prediction)
        prediction["calibration"] = self._calibration(prediction, fixture_id=fixture_id)
        return feature_vector, prediction

    def build_default_predictions(self) -> dict[str, Any]:
        fixture_ids = self.builder.default_fixture_ids()
        predictions: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for fixture_id in fixture_ids:
            try:
                predictions.append(self.build_prediction(fixture_id))
            except (PreMatchResearchFeatureError, ValueError) as exc:
                failed.append({
                    "fixture_id": fixture_id,
                    "status": "failed",
                    "reason": str(exc),
                })
        report = build_pre_match_scoring_quality_report(
            fixture_ids=fixture_ids,
            predictions=predictions,
            failed=failed,
            expected_predictions=self.expected_default_predictions,
        )
        return {
            "status": report["status"],
            "version": P0_15_VERSION,
            "weights_version": P0_15_WEIGHTS_VERSION,
            "generated_at": report["generated_at"],
            "fixture_ids": fixture_ids,
            "predictions_count": len(predictions),
            "failed_count": len(failed),
            "predictions": predictions,
            "quality_report": report,
        }

    def write_default_artifacts(
        self,
        *,
        predictions_path: Path = DEFAULT_P0_15_PREDICTIONS_PATH,
        quality_report_path: Path = DEFAULT_P0_15_QUALITY_REPORT_PATH,
        quality_report_md_path: Path = DEFAULT_P0_15_QUALITY_REPORT_MD_PATH,
    ) -> dict[str, Any]:
        result = self.build_default_predictions()
        predictions_payload = {
            "version": result["version"],
            "weights_version": result["weights_version"],
            "generated_at": result["generated_at"],
            "not_used_in_production_scoring_by_default": True,
            "predictions_count": result["predictions_count"],
            "failed_count": result["failed_count"],
            "predictions": result["predictions"],
        }
        _write_json(predictions_path, predictions_payload)
        _write_json(quality_report_path, result["quality_report"])
        quality_report_md_path.parent.mkdir(parents=True, exist_ok=True)
        quality_report_md_path.write_text(
            format_pre_match_scoring_quality_report_markdown(result["quality_report"]),
            encoding="utf-8",
        )
        return {
            **result,
            "artifacts": {
                "predictions_path": str(predictions_path),
                "quality_report_path": str(quality_report_path),
                "quality_report_md_path": str(quality_report_md_path),
            },
        }

    def _calibration(self, prediction: dict[str, Any], *, fixture_id: str) -> dict[str, Any]:
        reviews = (
            self.local_store_repository.list_post_match_reviews()
            if self.local_store_repository is not None
            else []
        )
        calibration = build_bayesian_calibration(
            prediction,
            reviews,
            exclude_match_id=fixture_id,
        )
        if self.local_store_repository is None:
            calibration["reason_codes"] = [
                "local_store_repository_unavailable",
                *list(calibration.get("reason_codes") or []),
            ]
        return calibration


def build_pre_match_scoring_quality_report(
    *,
    fixture_ids: list[str],
    predictions: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    expected_predictions: int = 12,
) -> dict[str, Any]:
    generated_expected_count = len(predictions) == expected_predictions
    fixture_scope_expected = len(fixture_ids) == expected_predictions
    status = "ok" if generated_expected_count and fixture_scope_expected and not failed else "partial"
    component_status_counts: dict[str, dict[str, int]] = {
        dimension: {}
        for dimension in P0_15_COMPONENT_DIMENSIONS
    }
    odds_status_counts: dict[str, int] = {}
    coverage_status_counts: dict[str, int] = {}
    player_form_snapshots_used = 0
    confidence_values: list[float] = []
    prohibited_claim_hits: list[dict[str, str]] = []
    for prediction in predictions:
        coverage_status = str(prediction.get("coverage", {}).get("status") or "unknown")
        coverage_status_counts[coverage_status] = coverage_status_counts.get(coverage_status, 0) + 1
        odds_status = str(prediction.get("input_summary", {}).get("odds_status") or "unknown")
        odds_status_counts[odds_status] = odds_status_counts.get(odds_status, 0) + 1
        player_form_snapshots_used += int(
            prediction.get("input_summary", {}).get("player_form_snapshots_used") or 0
        )
        confidence_values.append(float(prediction.get("risk", {}).get("confidence") or 0.0))
        prohibited_claim_hits.extend(_prohibited_claim_hits(prediction))
        for component in prediction.get("components", []):
            dimension = str(component.get("dimension"))
            component_status = str(component.get("status") or "unknown")
            counts = component_status_counts.setdefault(dimension, {})
            counts[component_status] = counts.get(component_status, 0) + 1
    if prohibited_claim_hits:
        status = "blocked"
    return {
        "status": status,
        "version": P0_15_VERSION,
        "weights_version": P0_15_WEIGHTS_VERSION,
        "generated_at": utc_now(),
        "not_used_in_production_scoring_by_default": True,
        "expected_predictions": expected_predictions,
        "fixture_ids_count": len(fixture_ids),
        "predictions_count": len(predictions),
        "failed_count": len(failed),
        "fixture_ids": fixture_ids,
        "failed": failed,
        "component_dimensions": list(P0_15_COMPONENT_DIMENSIONS),
        "component_status_counts": component_status_counts,
        "coverage_status_counts": coverage_status_counts,
        "odds_status_counts": odds_status_counts,
        "player_form_snapshots_used": player_form_snapshots_used,
        "average_confidence": round(
            sum(confidence_values) / len(confidence_values),
            2,
        )
        if confidence_values
        else 0.0,
        "prohibited_claim_hits": prohibited_claim_hits,
        "readiness": {
            "llm_layer_ready": status == "ok"
            and not prohibited_claim_hits,
            "reason": (
                "structured_p0_15_predictions_available_for_llm_explanation"
                if status == "ok" and not prohibited_claim_hits
                else "prediction_generation_incomplete_or_blocked"
            ),
            "llm_may_explain_scores": True,
            "llm_must_not_generate_scores": True,
        },
    }


def format_pre_match_scoring_quality_report_markdown(report: dict[str, Any]) -> str:
    rows = [
        "# P0.15 Pre-Match Research Scoring Quality Report",
        "",
        f"- Status: `{report['status']}`",
        f"- Version: `{report['version']}`",
        f"- Weights version: `{report['weights_version']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Predictions: {report['predictions_count']} / {report['expected_predictions']}",
        f"- Failed: {report['failed_count']}",
        f"- Average confidence: {report['average_confidence']}",
        f"- Player form snapshots used: {report['player_form_snapshots_used']}",
        f"- LLM layer ready: {str(report['readiness']['llm_layer_ready']).lower()}",
        "",
        "## Component Status",
        "",
    ]
    for dimension, counts in report["component_status_counts"].items():
        status_text = ", ".join(
            f"{status}={count}"
            for status, count in sorted(counts.items())
        )
        rows.append(f"- `{dimension}`: {status_text or 'none'}")
    rows.extend([
        "",
        "## Odds Status",
        "",
    ])
    for status, count in sorted(report["odds_status_counts"].items()):
        rows.append(f"- `{status}`: {count}")
    if report["failed"]:
        rows.extend(["", "## Failed Fixtures", ""])
        for item in report["failed"]:
            rows.append(f"- `{item['fixture_id']}`: {item['reason']}")
    return "\n".join(rows) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prohibited_claim_hits(prediction: dict[str, Any]) -> list[dict[str, str]]:
    prohibited = ["必中", "稳赢", "稳赚", "保证收益", "无风险", "闭眼上", "梭哈"]
    text = json.dumps(prediction, ensure_ascii=False)
    return [
        {
            "fixture_id": str(prediction.get("fixture_id")),
            "word": word,
        }
        for word in prohibited
        if word in text
    ]
