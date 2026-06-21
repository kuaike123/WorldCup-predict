from __future__ import annotations

from datetime import datetime
from typing import Any

from src.scoring.field_normalizer import clamp


HOST_TEAM_MARKERS = {
    "mexico",
    "méxico",
    "mex",
    "united states",
    "usa",
    "us",
    "canada",
    "can",
}

REGIONAL_MARKERS = {
    "mex",
    "usa",
    "us",
    "can",
    "crc",
    "pan",
    "jam",
    "hon",
    "slv",
    "gua",
    "costa rica",
    "panama",
    "jamaica",
}

LEVELS = ("unavailable", "low", "medium", "high")


def build_world_cup_context_correction(
    fixture: dict[str, Any],
    home_team: dict[str, Any],
    away_team: dict[str, Any],
    *,
    motivation_context: dict[str, Any] | None = None,
    snapshots: list[dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Build an auditable context-only World Cup correction layer.

    The returned values are deliberately not weighted into P0.15 scoring. They are
    bounded context for explanation and later calibration.
    """

    if not _is_world_cup_fixture(fixture):
        return _unavailable_context("not_world_cup_fixture")

    snapshot_items = _snapshot_items(snapshots or [])
    news = _news_intelligence(snapshot_items)
    motivation = _motivation_block(fixture, motivation_context)
    tactical_draw = _tactical_draw_block(fixture, motivation, news)
    knockout_path = _knockout_path_block(fixture)
    environment_travel = _environment_travel_block(fixture, news)
    regional_advantage = _regional_advantage_block(home_team, away_team)

    quality = _context_quality(
        motivation=motivation,
        news=news,
        environment_travel=environment_travel,
        regional_advantage=regional_advantage,
        tactical_draw=tactical_draw,
    )
    status = _context_status(
        quality=quality,
        news=news,
        environment_travel=environment_travel,
        regional_advantage=regional_advantage,
        tactical_draw=tactical_draw,
    )

    return {
        "schema_version": "world_cup_context_correction.v1",
        "status": status,
        "quality_score": quality,
        "not_used_in_production_scoring_by_default": True,
        "motivation": motivation,
        "tactical_draw": tactical_draw,
        "knockout_path": knockout_path,
        "environment_travel": environment_travel,
        "regional_advantage": regional_advantage,
        "news_intelligence": news,
        "user_explanation": _user_explanation(
            motivation=motivation,
            tactical_draw=tactical_draw,
            knockout_path=knockout_path,
            environment_travel=environment_travel,
            regional_advantage=regional_advantage,
            news=news,
        ),
    }


def _unavailable_context(reason: str) -> dict[str, Any]:
    return {
        "schema_version": "world_cup_context_correction.v1",
        "status": "unavailable",
        "quality_score": 0.0,
        "not_used_in_production_scoring_by_default": True,
        "motivation": {"home_score": 0.0, "away_score": 0.0, "reason_codes": [reason]},
        "tactical_draw": {"index": 0.0, "level": "low", "reason_codes": [reason]},
        "knockout_path": {"incentive_score": 0.0, "interpretation": "low", "reason_codes": [reason]},
        "environment_travel": {
            "status": "unavailable",
            "quality_score": 0.0,
            "load_score_home": 0.0,
            "load_score_away": 0.0,
            "tempo_impact": "none",
            "reason_codes": [reason],
        },
        "regional_advantage": {"home_score": 0.0, "away_score": 0.0, "reason_codes": [reason]},
        "news_intelligence": {
            "status": "unavailable",
            "quality_score": 0.0,
            "rotation_risk": "unavailable",
            "lineup_uncertainty": "unavailable",
            "weather_impact": "unavailable",
            "market_sentiment": "unavailable",
            "reason_codes": [reason],
        },
        "user_explanation": ["世界杯特殊修正因素：当前比赛不在世界杯修正层适用范围内。"],
    }


def _motivation_block(
    fixture: dict[str, Any],
    motivation_context: dict[str, Any] | None,
) -> dict[str, Any]:
    home_score = float((motivation_context or {}).get("motivation_score_home") or 60.0)
    away_score = float((motivation_context or {}).get("motivation_score_away") or 60.0)
    reason_codes = set(str(item) for item in (motivation_context or {}).get("reason_codes", []))
    stage = _stage(fixture, motivation_context)

    if "group_stage" in stage:
        reason_codes.add("world_cup_48_team_group_format")
        if "matchday_3" in stage:
            reason_codes.add("third_place_advancement_pressure")
    if _flag(fixture, "must_win_home"):
        reason_codes.add("home_must_win")
        home_score += 6
    if _flag(fixture, "must_win_away"):
        reason_codes.add("away_must_win")
        away_score += 6
    if _flag(fixture, "home_already_qualified"):
        reason_codes.add("home_rotation_motivation")
        home_score -= 6
    if _flag(fixture, "away_already_qualified"):
        reason_codes.add("away_rotation_motivation")
        away_score -= 6

    return {
        "home_score": _score(home_score),
        "away_score": _score(away_score),
        "status": str((motivation_context or {}).get("status") or "partial"),
        "quality_score": _quality((motivation_context or {}).get("quality_score"), default=0.45),
        "stage": stage,
        "reason_codes": sorted(reason_codes) or ["motivation_context_limited"],
    }


def _tactical_draw_block(
    fixture: dict[str, Any],
    motivation: dict[str, Any],
    news: dict[str, Any],
) -> dict[str, Any]:
    stage = str(motivation.get("stage") or _stage(fixture, None))
    home_score = float(motivation.get("home_score") or 0.0)
    away_score = float(motivation.get("away_score") or 0.0)
    index = 28.0
    reason_codes: set[str] = {"world_cup_group_context"}

    if "group_stage" in stage:
        index += 8
    if "matchday_3" in stage:
        index += 18
        reason_codes.add("group_stage_final_round")
    if abs(home_score - away_score) <= 12 and home_score >= 50 and away_score >= 50:
        index += 12
        reason_codes.add("balanced_motivation_scores")
    if _flag(fixture, "draw_enough_home") or _flag(fixture, "draw_enough_away"):
        index += 18
        reason_codes.add("draw_can_be_acceptable")
    if _flag(fixture, "third_place_buffer"):
        index += 10
        reason_codes.add("third_place_buffer")
    if "news_draw_or_conservative_hint" in news.get("reason_codes", []):
        index += 10
        reason_codes.add("news_draw_or_conservative_hint")
    if "group_stage_matchday_1" in stage:
        reason_codes.add("standings_context_limited")

    score = _score(index)
    return {
        "index": score,
        "level": _risk_level(score),
        "reason_codes": sorted(reason_codes),
        "threshold_explanation": "0-39：战术平局风险低；40-64：需要防平；65+：平局与小比分权重应被重点解释。",
    }


def _knockout_path_block(fixture: dict[str, Any]) -> dict[str, Any]:
    score = 10.0
    reason_codes: set[str] = {"path_context_low"}
    stage = _stage(fixture, None)
    if "matchday_3" in stage:
        score += 15
        reason_codes.add("final_group_rank_can_change_path")
    if _flag(fixture, "home_already_qualified") or _flag(fixture, "away_already_qualified"):
        score += 20
        reason_codes.add("qualified_team_path_choice_possible")
    if _flag(fixture, "avoid_strong_opponent_path"):
        score += 30
        reason_codes.add("avoid_strong_opponent_path")
    bounded = _score(score)
    return {
        "incentive_score": bounded,
        "interpretation": _risk_level(bounded),
        "reason_codes": sorted(reason_codes),
    }


def _environment_travel_block(fixture: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    reason_codes: set[str] = set()
    home_load = 22.0
    away_load = 22.0
    quality = 0.0

    temperature = _numeric_field(fixture, "temperature_c", "weather_temperature_c", "heat_index_c")
    humidity = _numeric_field(fixture, "humidity_percent", "weather_humidity_percent")
    if temperature is not None:
        quality = max(quality, 0.4)
        if temperature >= 30:
            home_load += 12
            away_load += 12
            reason_codes.add("high_temperature_tempo_down")
    if humidity is not None:
        quality = max(quality, 0.4)
        if humidity >= 70:
            home_load += 10
            away_load += 10
            reason_codes.add("high_humidity_fatigue")

    home_travel = _numeric_field(fixture, "home_travel_km", "home_flight_km")
    away_travel = _numeric_field(fixture, "away_travel_km", "away_flight_km")
    if home_travel is not None:
        quality = max(quality, 0.45)
        home_load += min(home_travel / 250, 18)
        reason_codes.add("home_travel_distance_available")
    if away_travel is not None:
        quality = max(quality, 0.45)
        away_load += min(away_travel / 250, 18)
        reason_codes.add("away_travel_distance_available")

    if news.get("weather_impact") in {"medium", "high"}:
        quality = max(quality, float(news.get("quality_score") or 0.0))
        home_load += 8
        away_load += 8
        reason_codes.add("news_weather_or_pitch_hint")
    if "news_travel_hint" in news.get("reason_codes", []):
        quality = max(quality, float(news.get("quality_score") or 0.0))
        away_load += 6
        reason_codes.add("news_travel_hint")

    if not reason_codes:
        return {
            "status": "unavailable",
            "quality_score": 0.0,
            "load_score_home": 0.0,
            "load_score_away": 0.0,
            "tempo_impact": "none",
            "reason_codes": ["weather_travel_context_unavailable"],
        }

    tempo_impact = "lower_tempo" if home_load >= 38 or away_load >= 38 else "none"
    if abs(home_load - away_load) >= 15:
        tempo_impact = "higher_volatility"
    return {
        "status": "partial" if quality < 0.65 else "ok",
        "quality_score": round(quality, 3),
        "load_score_home": _score(home_load),
        "load_score_away": _score(away_load),
        "tempo_impact": tempo_impact,
        "reason_codes": sorted(reason_codes),
    }


def _regional_advantage_block(home_team: dict[str, Any], away_team: dict[str, Any]) -> dict[str, Any]:
    home_markers = _team_markers(home_team)
    away_markers = _team_markers(away_team)
    home_score = 50.0
    away_score = 50.0
    reason_codes: set[str] = set()

    if home_markers & HOST_TEAM_MARKERS:
        home_score += 18
        reason_codes.add("home_host_country_advantage")
    if away_markers & HOST_TEAM_MARKERS:
        away_score += 18
        reason_codes.add("away_host_country_advantage")
    if home_markers & REGIONAL_MARKERS:
        home_score += 6
        reason_codes.add("home_regional_adaptation")
    if away_markers & REGIONAL_MARKERS:
        away_score += 6
        reason_codes.add("away_regional_adaptation")

    if not reason_codes:
        reason_codes.add("regional_advantage_neutral_or_unknown")
    return {
        "home_score": _score(home_score),
        "away_score": _score(away_score),
        "reason_codes": sorted(reason_codes),
    }


def _news_intelligence(items: list[dict[str, Any]]) -> dict[str, Any]:
    reason_codes: set[str] = set()
    quality = 0.0
    rotation = "unavailable"
    lineup = "unavailable"
    weather = "unavailable"
    market = "unavailable"

    for item in items:
        confidence = _quality(item.get("confidence"), default=0.55)
        quality = max(quality, confidence)
        text = _item_text(item)
        category = str(item.get("category") or "").casefold()

        if _has_any(text, category, ("rotation", "rotate", "替补", "轮换", "rest starters")):
            rotation = _max_level(rotation, "high" if confidence >= 0.7 else "medium")
            reason_codes.add("news_rotation_hint")
        if _has_any(text, category, ("lineup", "starting xi", "projected", "首发", "阵容", "injury", "伤", "停赛")):
            lineup = _max_level(lineup, "high" if "injury" in text or "伤" in text else "medium")
            reason_codes.add("news_lineup_or_injury_hint")
        if _has_any(text, category, ("draw", "conservative", "平局", "保守", "满意平局", "小球")):
            reason_codes.add("news_draw_or_conservative_hint")
        if _has_any(text, category, ("weather", "temperature", "humidity", "pitch", "高温", "湿度", "草皮", "暴雨")):
            weather = _max_level(weather, "high" if confidence >= 0.7 else "medium")
            reason_codes.add("news_weather_or_pitch_hint")
        if _has_any(text, category, ("travel", "flight", "arrive", "时差", "航班", "旅程", "奔波")):
            reason_codes.add("news_travel_hint")
        if _has_any(text, category, ("odds", "market", "盘口", "水位", "成交", "升盘", "降盘")):
            market = "hot" if _has_any(text, category, ("hot", "升温", "热门", "steam")) else "balanced"
            reason_codes.add("news_market_sentiment_hint")

    if not items:
        return {
            "status": "unavailable",
            "quality_score": 0.0,
            "rotation_risk": "unavailable",
            "lineup_uncertainty": "unavailable",
            "weather_impact": "unavailable",
            "market_sentiment": "unavailable",
            "reason_codes": ["news_intelligence_unavailable"],
        }
    return {
        "status": "partial" if reason_codes else "unavailable",
        "quality_score": round(quality, 3) if reason_codes else 0.0,
        "rotation_risk": rotation,
        "lineup_uncertainty": lineup,
        "weather_impact": weather,
        "market_sentiment": market,
        "reason_codes": sorted(reason_codes) if reason_codes else ["news_intelligence_unclassified"],
    }


def _user_explanation(
    *,
    motivation: dict[str, Any],
    tactical_draw: dict[str, Any],
    knockout_path: dict[str, Any],
    environment_travel: dict[str, Any],
    regional_advantage: dict[str, Any],
    news: dict[str, Any],
) -> list[str]:
    explanations = [
        (
            "出线战意："
            f"主队 {motivation['home_score']}/100，客队 {motivation['away_score']}/100；"
            "该分数只解释世界杯赛制和战意背景，不直接改写胜平负概率。"
        ),
        (
            "战术平局风险："
            f"{tactical_draw['index']}/100（{tactical_draw['level']}）；"
            "若双方都能接受不输球，后段节奏可能下降。"
        ),
        (
            "路径选择动机："
            f"{knockout_path['incentive_score']}/100（{knockout_path['interpretation']}）；"
            "只有在排名和潜在对手较清晰时才应提高解释权重。"
        ),
    ]
    if environment_travel["status"] == "unavailable":
        explanations.append("天气与旅程：暂无可靠结构化数据，本场不做节奏加权，只保留观察项。")
    else:
        explanations.append(
            "天气与旅程："
            f"主队负荷 {environment_travel['load_score_home']}/100，"
            f"客队负荷 {environment_travel['load_score_away']}/100，"
            f"节奏影响为 {environment_travel['tempo_impact']}。"
        )
    regional_gap = float(regional_advantage["home_score"]) - float(regional_advantage["away_score"])
    if abs(regional_gap) >= 10:
        side = "主队" if regional_gap > 0 else "客队"
        explanations.append(f"区域优势：{side}存在主办国或区域适应优势，需要在赛前分析中单独解释。")
    if news["status"] != "unavailable":
        explanations.append(
            "新闻情报：已转成轮换、阵容、天气或盘口标签；不会直接把单篇新闻写成结论。"
        )
    return explanations


def _context_quality(
    *,
    motivation: dict[str, Any],
    news: dict[str, Any],
    environment_travel: dict[str, Any],
    regional_advantage: dict[str, Any],
    tactical_draw: dict[str, Any],
) -> float:
    values = [
        float(motivation.get("quality_score") or 0.0),
        float(news.get("quality_score") or 0.0),
        float(environment_travel.get("quality_score") or 0.0),
    ]
    if "regional_advantage_neutral_or_unknown" not in regional_advantage.get("reason_codes", []):
        values.append(0.65)
    if tactical_draw.get("reason_codes"):
        values.append(0.45)
    return round(max(values), 3)


def _context_status(
    *,
    quality: float,
    news: dict[str, Any],
    environment_travel: dict[str, Any],
    regional_advantage: dict[str, Any],
    tactical_draw: dict[str, Any],
) -> str:
    if quality <= 0:
        return "unavailable"
    has_specific_context = (
        news["status"] != "unavailable"
        or environment_travel["status"] != "unavailable"
        or "regional_advantage_neutral_or_unknown" not in regional_advantage.get("reason_codes", [])
        or "group_stage_final_round" in tactical_draw.get("reason_codes", [])
    )
    if quality >= 0.65 and has_specific_context:
        return "ok"
    return "partial"


def _snapshot_items(snapshots: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not snapshot:
            continue
        for item in snapshot.get("items") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def _stage(fixture: dict[str, Any], motivation_context: dict[str, Any] | None) -> str:
    context_stage = str((motivation_context or {}).get("stage") or "")
    if context_stage:
        return context_stage
    values = [
        fixture.get("stage"),
        fixture.get("round"),
        fixture.get("matchday"),
        fixture.get("competition"),
    ]
    text = " ".join(str(value or "") for value in values).casefold()
    fixture_id = str(fixture.get("fixture_id") or fixture.get("match_id") or "").casefold()
    if "opening" in fixture_id or _match_date(fixture) == "2026-06-11":
        return "group_stage_matchday_1"
    if "matchday 3" in text or "matchday_3" in text or "last round" in text:
        return "group_stage_matchday_3"
    if "knockout" in text or "round of" in text or "quarter" in text or "semi" in text or "final" in text:
        return "knockout_stage"
    if "group" in text or "world cup" in text:
        return "group_stage"
    return "unknown"


def _is_world_cup_fixture(fixture: dict[str, Any]) -> bool:
    text = " ".join(
        str(fixture.get(key) or "")
        for key in ("competition", "season", "source", "fixture_id", "match_id")
    ).casefold()
    return "world cup" in text or "wc2026" in text or "fixture_wc2026" in text


def _team_markers(team: dict[str, Any]) -> set[str]:
    return {
        str(value).casefold()
        for value in (
            team.get("canonical_name"),
            team.get("name"),
            team.get("country_code"),
            team.get("fifa_code"),
            team.get("source_team_id"),
        )
        if str(value or "").strip()
    }


def _flag(fixture: dict[str, Any], flag: str) -> bool:
    flags = fixture.get("motivation_flags")
    if isinstance(flags, list):
        return flag in {str(item) for item in flags}
    return bool(fixture.get(flag))


def _numeric_field(fixture: dict[str, Any], *keys: str) -> float | None:
    weather = fixture.get("weather") if isinstance(fixture.get("weather"), dict) else {}
    for key in keys:
        value = fixture.get(key)
        if value is None:
            value = weather.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _item_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("category", "type", "title", "summary", "description")
    ).casefold()


def _has_any(text: str, category: str, tokens: tuple[str, ...]) -> bool:
    joined = f"{category} {text}"
    return any(token.casefold() in joined for token in tokens)


def _max_level(current: str, candidate: str) -> str:
    return LEVELS[max(LEVELS.index(current), LEVELS.index(candidate))]


def _score(value: float) -> float:
    return round(clamp(float(value), 0.0, 100.0), 2)


def _quality(value: Any, *, default: float) -> float:
    if isinstance(value, (int, float)):
        return round(clamp(float(value), 0.0, 1.0), 3)
    return default


def _risk_level(score: float) -> str:
    if score >= 65:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _match_date(fixture: dict[str, Any]) -> str | None:
    value = str(fixture.get("match_time") or fixture.get("kickoff_time") or "")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None
