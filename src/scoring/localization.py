from __future__ import annotations

from copy import deepcopy
from typing import Any


TEAM_NAMES = {
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia and Herzegovina": "波黑",
    "Saudi Arabia": "沙特阿拉伯",
    "Canada": "加拿大",
    "Cameroon": "喀麦隆",
    "Colombia": "哥伦比亚",
    "Congo DR": "刚果（金）",
    "DR Congo": "刚果（金）",
    "Democratic Republic of the Congo": "刚果（金）",
    "Croatia": "克罗地亚",
    "Denmark": "丹麦",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Brazil": "巴西",
    "Germany": "德国",
    "Ghana": "加纳",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "Nigeria": "尼日利亚",
    "Paraguay": "巴拉圭",
    "Panama": "巴拿马",
    "Peru": "秘鲁",
    "Poland": "波兰",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Senegal": "塞内加尔",
    "Serbia": "塞尔维亚",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Spain": "西班牙",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "USA": "美国",
    "United States": "美国",
    "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦",
    "Home Example": "主队样例",
    "Away Example": "客队样例",
    "Home FC": "主队样例",
    "Away FC": "客队样例",
    "Other FC": "其他球队样例",
}

DIMENSION_LABELS = {
    "team_strength": "球队综合实力",
    "recent_form": "近期状态",
    "lineup_integrity": "阵容完整度",
    "key_player_status": "核心球员状态",
    "attack_defense_efficiency": "攻防效率",
    "schedule_fatigue": "赛程体能",
    "motivation_stage": "战意与赛制",
    "odds_movement": "盘口赔率变化",
}

MARKET_DIRECTIONS = {
    "home_market_positive": "主队方向获得市场支持",
    "away_market_positive": "客队方向获得市场支持",
    "neutral": "市场信号偏中性",
    "neutral_low_quality": "市场信号偏中性，数据质量较低",
}

STRENGTH_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

RISK_FLAGS = {
    "home_heat_risk": "主队热度偏高",
    "away_heat_risk": "客队热度偏高",
    "over_heat_risk": "大球热度偏高",
    "risk_control_movement": "盘口有风控调整迹象",
    "information_movement": "多家机构同向变化",
    "high_odds_dispersion": "机构分歧较大",
    "odds_data_insufficient": "盘口数据不足",
}

REASON_CODES = {
    "close_score_gap": "双方评分接近，胜负边界不明显",
    "market_fundamental_conflict": "市场方向与基本面存在冲突",
    "market_heat_risk": "市场热度偏高，需防范过热风险",
    "high_missing_field_ratio": "关键数据缺失较多",
    "high_odds_dispersion": "不同机构赔率分歧较大",
    "knockout_or_life_death_stage": "淘汰赛或关键战，比赛波动更高",
    "upset_risk_high": "冷门风险偏高",
    "low_confidence": "置信度偏低",
    "no_major_extra_risk": "暂无明显额外风险",
}

SOURCE_LABELS = {
    "local_sample": "本地样例数据",
    "mock_sample": "本地样例赔率",
    "api_sports": "API-Sports",
    "sportradar_soccer": "Sportradar",
    "the_odds_api": "The Odds API",
    "external": "外部数据源",
}

STATUS_LABELS = {
    "mock_pre_match": "赛前样例状态",
    "ok": "可用",
    "partial": "部分可用",
    "unavailable": "缺失",
    "blocked": "已阻断",
    "stale": "已过时",
    "unknown": "状态未知",
}

COMPETITION_LABELS = {
    "World Cup": "世界杯",
    "FIFA World Cup": "世界杯",
    "FIFA World Cup™": "世界杯",
    "2026 FIFA World Cup": "世界杯",
    "FIFA World Cup 2026": "世界杯",
    "FIFA World Cup 2026™": "世界杯",
    "Friendly": "友谊赛",
    "International Friendly": "国际友谊赛",
}

STAGE_LABELS = {
    "opening": "开幕战",
    "group stage": "小组赛",
    "knockout": "淘汰赛",
    "unknown": "阶段待确认",
}


def team_name(name: str | None) -> str:
    if not name:
        return ""
    return TEAM_NAMES.get(name, name)


def match_label(home_team: str, away_team: str) -> str:
    return f"{team_name(home_team)}对阵{team_name(away_team)}"


def competition_label(name: str | None) -> str:
    if not name:
        return "赛事待确认"
    return COMPETITION_LABELS.get(name, name)


def stage_label(name: str | None) -> str:
    if not name:
        return "阶段待确认"
    lowered = str(name).strip().casefold()
    return STAGE_LABELS.get(lowered, str(name))


def dimension_label(code: str) -> str:
    return DIMENSION_LABELS.get(code, code)


def market_direction(code: str | None) -> str:
    if not code:
        return "市场信号未知"
    return MARKET_DIRECTIONS.get(code, code)


def strength_label(code: str | None) -> str:
    if not code:
        return "未知"
    return STRENGTH_LABELS.get(code, code)


def risk_flag(code: str) -> str:
    return RISK_FLAGS.get(code, code)


def reason_code(code: str) -> str:
    return REASON_CODES.get(code, code)


def source_label(code: str | None) -> str:
    if not code:
        return "未知来源"
    return SOURCE_LABELS.get(code, code)


def status_label(code: str | None) -> str:
    if not code:
        return "状态未知"
    return STATUS_LABELS.get(code, code)


def localize_team_text(text: str) -> str:
    result = text
    for english, chinese in TEAM_NAMES.items():
        result = result.replace(english, chinese)
    return result


def market_explanation(market: dict[str, Any]) -> str:
    return (
        f"{market_direction(str(market.get('direction')))}，"
        f"强度{strength_label(str(market.get('strength')))}，"
        f"综合分 {float(market.get('score', 0)):.1f}。"
    )


def localized_analysis_view(analysis: dict[str, Any]) -> dict[str, Any]:
    view = deepcopy(analysis)
    view["home_team"] = team_name(str(view.get("home_team", "")))
    view["away_team"] = team_name(str(view.get("away_team", "")))
    view["risk_level"] = strength_label(str(view.get("risk_level", "")))
    view["main_direction"] = localize_team_text(str(view.get("main_direction", "")))
    view["secondary_direction"] = localize_team_text(str(view.get("secondary_direction", "")))
    view["reason_codes"] = [reason_code(str(code)) for code in view.get("reason_codes", [])]
    view["explanation"] = (
        f"{view['home_team']} 综合分 {view.get('home_score')}，"
        f"{view['away_team']} 综合分 {view.get('away_score')}，"
        f"分差 {view.get('score_gap')}。"
        f"市场信号为 {market_direction(str(analysis.get('market_signal', {}).get('direction')))}，"
        f"风险等级 {view['risk_level']}。"
    )

    market = view.get("market_signal")
    if isinstance(market, dict):
        market["direction"] = market_direction(str(market.get("direction")))
        market["strength"] = strength_label(str(market.get("strength")))
        market["risk_flags"] = [risk_flag(str(code)) for code in market.get("risk_flags", [])]
        market["explanation"] = market_explanation(analysis.get("market_signal", {}))

    for item in view.get("dimension_breakdown", []):
        if isinstance(item, dict):
            item["dimension"] = dimension_label(str(item.get("dimension", "")))
            item["explanation"] = localize_team_text(str(item.get("explanation", "")))
            for side in ("home_raw_value", "away_raw_value"):
                raw_value = item.get(side)
                if isinstance(raw_value, dict) and {"direction", "strength", "risk_flags"}.issubset(raw_value):
                    raw_value["direction"] = market_direction(str(raw_value.get("direction")))
                    raw_value["strength"] = strength_label(str(raw_value.get("strength")))
                    raw_value["risk_flags"] = [risk_flag(str(code)) for code in raw_value.get("risk_flags", [])]
                    raw_value["explanation"] = market_explanation(analysis.get("market_signal", {}))

    snapshot = view.get("input_snapshot")
    if isinstance(snapshot, dict):
        if "home_team" in snapshot:
            snapshot["home_team"] = team_name(str(snapshot["home_team"]))
        if "away_team" in snapshot:
            snapshot["away_team"] = team_name(str(snapshot["away_team"]))
    return localize_public_values(view)


def localize_public_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: localize_public_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [localize_public_values(item) for item in value]
    if isinstance(value, str):
        return localize_public_text(value)
    return value


def localize_public_text(text: str) -> str:
    exact_maps = {
        **TEAM_NAMES,
        **DIMENSION_LABELS,
        **MARKET_DIRECTIONS,
        **STRENGTH_LABELS,
        **RISK_FLAGS,
        **REASON_CODES,
        **SOURCE_LABELS,
        **STATUS_LABELS,
        **COMPETITION_LABELS,
        **STAGE_LABELS,
        "low": "低",
        "medium": "中",
        "high": "高",
        "none": "暂无",
    }
    if text in exact_maps:
        return exact_maps[text]
    result = text
    replacements = {
        **TEAM_NAMES,
        **DIMENSION_LABELS,
        **MARKET_DIRECTIONS,
        **STRENGTH_LABELS,
        **RISK_FLAGS,
        **REASON_CODES,
        **COMPETITION_LABELS,
        "home_market_positive": "主队方向获得市场支持",
        "away_market_positive": "客队方向获得市场支持",
        "neutral_low_quality": "市场信号偏中性，数据质量较低",
        "neutral": "市场信号偏中性",
        "low": "低",
        "medium": "中",
        "high": "高",
        "unavailable": "缺失",
        "partial": "部分可用",
        "blocked": "已阻断",
        "stale": "已过时",
        "World Cup": "世界杯",
        "Friendly": "友谊赛",
        " vs ": " 对阵 ",
        "VIP": "专享版",
        "Unknown": "待确认",
    }
    for old, new in replacements.items():
        result = result.replace(old, new)
    result = result.replace("市场信号 主队方向获得市场支持，强度 中", "主队方向获得市场支持，强度中")
    result = result.replace("市场信号 客队方向获得市场支持，强度 中", "客队方向获得市场支持，强度中")
    result = result.replace("市场信号 市场信号偏中性，强度 低", "市场信号偏中性，强度低")
    return result
