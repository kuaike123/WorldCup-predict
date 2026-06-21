from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .localization import (
    dimension_label,
    localize_team_text,
    market_direction,
    market_explanation,
    match_label,
    reason_code,
    risk_flag,
    strength_label,
    team_name,
)


ROOT = Path(__file__).resolve().parents[2]


def build_report(analysis: dict[str, Any]) -> str:
    market = analysis["market_signal"]
    reasons = analysis["reason_codes"] or ["no_major_extra_risk"]
    core_basis = analysis["dimension_breakdown"][:4]
    home_team = team_name(analysis["home_team"])
    away_team = team_name(analysis["away_team"])
    basis_lines = [
        (
            f"{idx}. {dimension_label(item['dimension'])}："
            f"{home_team} {item['home_score']} / {away_team} {item['away_score']}，"
            f"数据质量分 {item['quality_score']:.2f}。"
        )
        for idx, item in enumerate(core_basis, 1)
    ]
    risk_lines = [f"- {reason_code(code)}" for code in reasons]
    risk_flags = [risk_flag(code) for code in market["risk_flags"]]
    return "\n".join([
        "【赛前分析】",
        f"比赛：{match_label(analysis['home_team'], analysis['away_team'])}",
        f"时间：{analysis['input_snapshot']['match_time']}",
        "",
        "核心结论：",
        f"- 主方向：{localize_team_text(analysis['main_direction'])}",
        f"- 次方向：{localize_team_text(analysis['secondary_direction'])}",
        f"- 推荐比分：{' / '.join(analysis['recommended_scores'])}",
        f"- 置信度：{analysis['confidence']}",
        f"- 风险等级：{strength_label(analysis['risk_level'])}",
        "",
        "评分摘要：",
        f"- {home_team} 综合分：{analysis['home_score']}",
        f"- {away_team} 综合分：{analysis['away_score']}",
        f"- 分差：{analysis['score_gap']}",
        f"- 胜平负概率：{analysis['probabilities']['home_win']:.1%} / {analysis['probabilities']['draw']:.1%} / {analysis['probabilities']['away_win']:.1%}",
        f"- 大 2.5 概率：{analysis['probabilities']['over_2_5']:.1%}",
        "",
        "核心依据：",
        *basis_lines,
        "",
        "市场信号：",
        f"- 方向：{market_direction(market['direction'])}",
        f"- 强度：{strength_label(market['strength'])}",
        f"- 风险标记：{', '.join(risk_flags) if risk_flags else '暂无明显风险标记'}",
        f"- 解释：{market_explanation(market)}",
        "",
        "风险提醒：",
        *risk_lines,
        "",
        "免责声明：",
        analysis["disclaimer"],
        "",
    ])


def save_outputs(analysis: dict[str, Any], output_root: Path | None = None) -> tuple[Path, Path]:
    root = output_root or ROOT / "outputs"
    result_dir = root / "analysis_results"
    report_dir = root / "reports"
    result_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    match_id = analysis["match_id"]
    result_path = result_dir / f"{match_id}.json"
    report_path = report_dir / f"{match_id}.md"
    result_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(build_report(analysis), encoding="utf-8")
    return result_path, report_path
