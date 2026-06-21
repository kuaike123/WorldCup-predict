from __future__ import annotations

from collections import defaultdict
from typing import Any


SHOT_XG = {
    "penalty": 0.75,
    "six_yard_box": 0.45,
    "one_on_one": 0.35,
    "central_box": 0.22,
    "wide_box": 0.12,
    "header_in_box": 0.10,
    "direct_free_kick": 0.06,
    "long_shot": 0.03,
    "unknown": 0.05,
}

EVENT_TYPE_ALIASES = {
    "attempt": "shot",
    "shot_on_goal": "shot",
    "shot_on_target": "shot",
    "shot_off_target": "shot",
    "shot_saved": "shot",
    "shot_blocked": "shot",
    "penalty": "shot",
    "free_kick_shot": "shot",
    "key_pass": "pass_to_shot",
    "shot_assist": "pass_to_shot",
    "assist": "assist",
    "goal": "goal",
    "yellow_card": "card",
    "red_card": "card",
    "substitution": "substitution",
    "substitution_in": "substitution",
    "substitution_out": "substitution",
}

RAW_TEXT_KEYS = {"raw", "raw_text", "description", "text", "commentary", "provider_payload"}
SAFE_QUALIFIER_KEYS = {
    "card_type",
    "description_flags",
    "goal_event_id",
    "in_box",
    "is_big_chance",
    "is_big_chance_text",
    "is_direct_free_kick",
    "is_header",
    "is_key_pass",
    "is_one_on_one",
    "is_penalty",
    "is_shot_assist",
    "is_six_yard_box",
    "outcome",
    "recipient_player_id",
    "shot_class",
    "shot_event_id",
    "shot_location",
    "shot_method",
    "simplified_xg",
    "substitution_role",
}
BIG_CHANCE_TERMS = (
    "big chance",
    "clear chance",
    "one-on-one",
    "one on one",
    "sitter",
    "绝佳机会",
    "重大机会",
    "单刀",
)


def build_advanced_metrics_proxy(
    match_id: str,
    *,
    timeline_events: list[dict[str, Any]] | None = None,
    commentary_events: list[dict[str, Any] | str] | None = None,
    source: str = "post_match_advanced_metrics_proxy",
) -> dict[str, Any]:
    events = normalize_post_match_events(
        match_id,
        timeline_events=timeline_events or [],
        commentary_events=commentary_events or [],
        source=source,
    )
    summary = calculate_advanced_metrics(match_id, events)
    return {
        "match_id": match_id,
        "proxy": True,
        "not_official_xg": True,
        "raw_saved": False,
        "normalized_events": events,
        "summary": summary,
    }


def normalize_post_match_events(
    match_id: str,
    *,
    timeline_events: list[dict[str, Any]],
    commentary_events: list[dict[str, Any] | str],
    source: str = "post_match_advanced_metrics_proxy",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(timeline_events, start=1):
        normalized.extend(_normalize_timeline_event(match_id, event, index=index, source=source))
    offset = len(timeline_events)
    for index, event in enumerate(commentary_events, start=1):
        normalized.extend(_normalize_commentary_event(match_id, event, index=offset + index, source=source))
    return _dedupe_events(normalized)


def calculate_advanced_metrics(match_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    enriched_events = [_enrich_event(event) for event in events]
    shots = [event for event in enriched_events if event["event_type"] == "shot"]
    passes = [event for event in enriched_events if event["event_type"] == "pass_to_shot"]
    shots_by_id = {event["event_id"]: event for event in shots}
    linked_passes: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    unlinked_passes = 0

    for pass_event in passes:
        linked_shot = _linked_shot(pass_event, shots_by_id, shots)
        linked_passes.append((pass_event, linked_shot))
        if linked_shot is None:
            unlinked_passes += 1

    team_metrics: dict[str, dict[str, Any]] = defaultdict(_metric_bucket)
    player_metrics: dict[str, dict[str, Any]] = defaultdict(_metric_bucket)

    for shot in shots:
        qualifier = _qualifier(shot)
        team_id = _entity_id(shot.get("team_id"), "unknown_team")
        player_id = _entity_id(shot.get("player_id"), "unknown_player")
        xg = float(qualifier["simplified_xg"])
        big = bool(qualifier["is_big_chance"])
        for bucket in (team_metrics[team_id], player_metrics[player_id]):
            bucket["shots"] += 1
            bucket["simplified_xg"] += xg
            bucket["big_chances"] += 1 if big else 0

    for pass_event, linked_shot in linked_passes:
        team_id = _entity_id(pass_event.get("team_id"), "unknown_team")
        player_id = _entity_id(pass_event.get("player_id"), "unknown_player")
        xa = float(_qualifier(linked_shot).get("simplified_xg", 0.0)) if linked_shot else 0.0
        created_big = bool(_qualifier(linked_shot).get("is_big_chance", False)) if linked_shot else False
        for bucket in (team_metrics[team_id], player_metrics[player_id]):
            bucket["key_passes"] += 1
            bucket["shot_assists"] += 1
            bucket["simplified_xa"] += xa
            bucket["big_chances_created"] += 1 if created_big else 0

    teams = _finalize_metric_map(team_metrics)
    players = _finalize_metric_map(player_metrics)
    totals = _total_metrics(teams, players)
    data_gaps = _data_gaps(events, shots, unlinked_passes)

    return {
        "match_id": match_id,
        "status": "ok" if shots else "no_shot_events",
        "source": "post_match_advanced_metrics_proxy",
        "proxy": True,
        "not_official_xg": True,
        "raw_saved": False,
        "events_count": len(enriched_events),
        "normalized_events_count": len(enriched_events),
        "totals": totals,
        "teams": teams,
        "players": players,
        "team_chance_quality": _ranked_team_quality(teams),
        "player_chance_creation": _ranked_player_creation(players),
        "top_xg_players": _ranked(players, "simplified_xg"),
        "top_xa_players": _ranked(players, "simplified_xa"),
        "top_key_pass_players": _ranked(players, "key_passes"),
        "biggest_chances": _biggest_chances(shots),
        "high_value_chance_timeline": _high_value_chance_timeline(shots),
        "data_gaps": data_gaps,
        "source_policy": {
            "proxy": True,
            "not_official_xg": True,
            "raw_saved": False,
            "raw_payload_saved": False,
            "not_used_in_scoring": True,
        },
    }


def _normalize_timeline_event(
    match_id: str,
    event: dict[str, Any],
    *,
    index: int,
    source: str,
) -> list[dict[str, Any]]:
    event_type = _event_type(event)
    base = _base_event(match_id, event, index=index, event_type=event_type, source=source)
    qualifier = _sanitize_qualifier(event.get("qualifier") if isinstance(event.get("qualifier"), dict) else {})
    qualifier.update(_derived_qualifier(event))
    base["qualifier"] = qualifier
    events = [base]

    if event_type == "shot":
        passer_id = _first_value(event, "passer_player_id", "assist_player_id", "shot_assist_player_id", "key_pass_player_id")
        if passer_id:
            pass_event_id = str(_first_value(event, "pass_event_id") or f"{base['event_id']}_pass")
            events.append({
                "match_id": match_id,
                "event_id": pass_event_id,
                "minute": base["minute"],
                "team_id": base["team_id"],
                "player_id": str(passer_id),
                "event_type": "pass_to_shot",
                "qualifier": {
                    "shot_event_id": base["event_id"],
                    "recipient_player_id": base["player_id"],
                    "is_key_pass": True,
                    "is_shot_assist": True,
                },
                "source": base["source"],
                "confidence": base["confidence"],
                "raw_saved": False,
            })
        if str(qualifier.get("outcome") or "").lower() == "goal" or bool(event.get("is_goal")):
            goal_event_id = str(_first_value(event, "goal_event_id") or f"{base['event_id']}_goal")
            events.append({
                "match_id": match_id,
                "event_id": goal_event_id,
                "minute": base["minute"],
                "team_id": base["team_id"],
                "player_id": base["player_id"],
                "event_type": "goal",
                "qualifier": {"shot_event_id": base["event_id"]},
                "source": base["source"],
                "confidence": base["confidence"],
                "raw_saved": False,
            })
            if passer_id:
                events.append({
                    "match_id": match_id,
                    "event_id": f"{goal_event_id}_assist",
                    "minute": base["minute"],
                    "team_id": base["team_id"],
                    "player_id": str(passer_id),
                    "event_type": "assist",
                    "qualifier": {"goal_event_id": goal_event_id, "shot_event_id": base["event_id"]},
                    "source": base["source"],
                    "confidence": base["confidence"],
                    "raw_saved": False,
                })
    return events


def _normalize_commentary_event(
    match_id: str,
    event: dict[str, Any] | str,
    *,
    index: int,
    source: str,
) -> list[dict[str, Any]]:
    payload = {"text": event} if isinstance(event, str) else dict(event)
    text = str(payload.get("text") or payload.get("description") or "")
    inferred_type = _infer_event_type_from_text(text) or _event_type(payload)
    base = _base_event(match_id, payload, index=index, event_type=inferred_type, source=f"{source}:commentary")
    qualifier = _sanitize_qualifier(payload.get("qualifier") if isinstance(payload.get("qualifier"), dict) else {})
    qualifier.update(_text_qualifier(text))
    qualifier.update(_derived_qualifier(payload))
    base["qualifier"] = qualifier
    return [base]


def _base_event(
    match_id: str,
    event: dict[str, Any],
    *,
    index: int,
    event_type: str,
    source: str,
) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "event_id": str(_first_value(event, "event_id", "id") or f"{match_id}_event_{index:03d}"),
        "minute": _minute(event.get("minute")),
        "team_id": _optional_str(_first_value(event, "team_id", "team")),
        "player_id": _optional_str(_first_value(event, "player_id", "player")),
        "event_type": event_type,
        "qualifier": {},
        "source": str(event.get("source") or source),
        "confidence": _confidence(event.get("confidence")),
        "raw_saved": False,
    }


def _event_type(event: dict[str, Any]) -> str:
    raw = str(_first_value(event, "event_type", "type") or "").strip().lower().replace(" ", "_")
    if raw in EVENT_TYPE_ALIASES:
        return EVENT_TYPE_ALIASES[raw]
    if "substitution" in raw or raw == "sub":
        return "substitution"
    if "card" in raw:
        return "card"
    if "goal" in raw:
        return "goal"
    if "assist" in raw and "shot" not in raw:
        return "assist"
    if "pass" in raw and "shot" in raw:
        return "pass_to_shot"
    if "shot" in raw or "attempt" in raw:
        return "shot"
    return raw or "unknown"


def _infer_event_type_from_text(text: str) -> str | None:
    lowered = text.lower()
    if any(term in lowered for term in ("substitution", "replaced", "换人")):
        return "substitution"
    if any(term in lowered for term in ("yellow card", "red card", "黄牌", "红牌")):
        return "card"
    if any(term in lowered for term in ("goal", "scores", "进球")):
        return "goal"
    if any(term in lowered for term in ("shot", "header", "free kick", "penalty", "射门", "头球", "任意球", "点球")):
        return "shot"
    if any(term in lowered or term in text for term in BIG_CHANCE_TERMS):
        return "shot"
    return None


def _derived_qualifier(event: dict[str, Any]) -> dict[str, Any]:
    qualifier: dict[str, Any] = {}
    for source_key, target_key in (
        ("shot_location", "shot_location"),
        ("location", "shot_location"),
        ("shot_method", "shot_method"),
        ("body_part", "shot_method"),
        ("outcome", "outcome"),
        ("card_type", "card_type"),
    ):
        value = event.get(source_key)
        if value is not None:
            qualifier[target_key] = _normalize_token(value)
    for key, target_key in (
        ("in_box", "in_box"),
        ("is_header", "is_header"),
        ("is_penalty", "is_penalty"),
        ("is_direct_free_kick", "is_direct_free_kick"),
        ("is_one_on_one", "is_one_on_one"),
        ("is_big_chance", "is_big_chance_text"),
        ("big_chance", "is_big_chance_text"),
    ):
        if key in event:
            qualifier[target_key] = bool(event.get(key))
    if str(event.get("event_type") or event.get("type") or "").lower() == "penalty":
        qualifier["is_penalty"] = True
    return qualifier


def _text_qualifier(text: str) -> dict[str, Any]:
    lowered = text.lower()
    terms = [term for term in BIG_CHANCE_TERMS if term in lowered or term in text]
    return {
        "is_big_chance_text": bool(terms),
        "is_one_on_one": any(term in lowered or term in text for term in ("one-on-one", "one on one", "单刀")),
        "is_header": any(term in lowered or term in text for term in ("header", "头球")),
        "is_penalty": any(term in lowered or term in text for term in ("penalty", "点球")),
        "is_direct_free_kick": any(term in lowered or term in text for term in ("free kick", "任意球")),
        "description_flags": terms,
    }


def _sanitize_qualifier(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _drop_raw_like_keys(item)
        for key, item in value.items()
        if str(key) in SAFE_QUALIFIER_KEYS and str(key) not in RAW_TEXT_KEYS
    }


def _drop_raw_like_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_raw_like_keys(item)
            for key, item in value.items()
            if str(key) in SAFE_QUALIFIER_KEYS and str(key) not in RAW_TEXT_KEYS and not _raw_like_key(str(key))
        }
    if isinstance(value, list):
        return [_drop_raw_like_keys(item) for item in value if not isinstance(item, dict) or _drop_raw_like_keys(item)]
    return value


def _raw_like_key(key: str) -> bool:
    return key.lower() in {"raw", "raw_text", "text", "commentary", "provider_payload", "payload", "raw_payload"}


def _enrich_event(event: dict[str, Any]) -> dict[str, Any]:
    enriched = {**event, "qualifier": dict(_qualifier(event)), "raw_saved": False}
    if enriched["event_type"] != "shot":
        return enriched
    shot_class = classify_shot(enriched)
    xg = SHOT_XG[shot_class]
    qualifier = enriched["qualifier"]
    qualifier["shot_class"] = shot_class
    qualifier["simplified_xg"] = xg
    qualifier["is_big_chance"] = _is_big_chance(qualifier, shot_class, xg)
    enriched["qualifier"] = qualifier
    return enriched


def classify_shot(event: dict[str, Any]) -> str:
    qualifier = _qualifier(event)
    location = _normalize_token(qualifier.get("shot_location"))
    method = _normalize_token(qualifier.get("shot_method"))
    in_box = bool(qualifier.get("in_box"))
    if qualifier.get("is_penalty"):
        return "penalty"
    if location in {"six_yard_box", "six-yard_box", "six_yard"} or qualifier.get("is_six_yard_box"):
        return "six_yard_box"
    if qualifier.get("is_one_on_one"):
        return "one_on_one"
    if qualifier.get("is_direct_free_kick") or method == "direct_free_kick":
        return "direct_free_kick"
    if (qualifier.get("is_header") or method == "header") and (in_box or location in {"central_box", "wide_box"}):
        return "header_in_box"
    if location in {"central_box", "center_box", "box_central"}:
        return "central_box"
    if location in {"wide_box", "box_wide"}:
        return "wide_box"
    if location in {"long_shot", "outside_box"} or in_box is False and location:
        return "long_shot"
    return "unknown"


def _is_big_chance(qualifier: dict[str, Any], shot_class: str, xg: float) -> bool:
    return (
        xg >= 0.30
        or bool(qualifier.get("is_penalty"))
        or bool(qualifier.get("is_one_on_one"))
        or shot_class == "six_yard_box"
        or bool(qualifier.get("is_big_chance_text"))
    )


def _linked_shot(
    pass_event: dict[str, Any],
    shots_by_id: dict[str, dict[str, Any]],
    shots: list[dict[str, Any]],
) -> dict[str, Any] | None:
    qualifier = _qualifier(pass_event)
    shot_id = qualifier.get("shot_event_id")
    if shot_id and str(shot_id) in shots_by_id:
        return shots_by_id[str(shot_id)]
    same_team = [
        shot for shot in shots
        if shot.get("team_id") == pass_event.get("team_id")
        and _minute_distance(shot.get("minute"), pass_event.get("minute")) <= 1
    ]
    return same_team[0] if same_team else None


def _metric_bucket() -> dict[str, Any]:
    return {
        "proxy": True,
        "not_official_xg": True,
        "shots": 0,
        "key_passes": 0,
        "shot_assists": 0,
        "big_chances": 0,
        "big_chances_created": 0,
        "simplified_xg": 0.0,
        "simplified_xa": 0.0,
        "chance_creation_score": 0.0,
    }


def _finalize_metric_map(metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    finalized: dict[str, dict[str, Any]] = {}
    for entity_id, values in metrics.items():
        item = dict(values)
        item["simplified_xg"] = round(float(item["simplified_xg"]), 3)
        item["simplified_xa"] = round(float(item["simplified_xa"]), 3)
        item["xg_per_shot"] = round(item["simplified_xg"] / item["shots"], 3) if item["shots"] else 0.0
        item["chance_creation_score"] = round(
            item["simplified_xa"] + item["key_passes"] * 0.1 + item["big_chances_created"] * 0.25,
            3,
        )
        finalized[entity_id] = item
    return finalized


def _total_metrics(teams: dict[str, dict[str, Any]], players: dict[str, dict[str, Any]]) -> dict[str, Any]:
    totals = _metric_bucket()
    for item in teams.values():
        for key in ("shots", "big_chances", "simplified_xg"):
            totals[key] += item[key]
    for item in players.values():
        for key in ("key_passes", "shot_assists", "big_chances_created", "simplified_xa", "chance_creation_score"):
            totals[key] += item[key]
    totals["simplified_xg"] = round(float(totals["simplified_xg"]), 3)
    totals["simplified_xa"] = round(float(totals["simplified_xa"]), 3)
    totals["chance_creation_score"] = round(float(totals["chance_creation_score"]), 3)
    return totals


def _ranked_team_quality(teams: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"team_id": entity_id, **values}
        for entity_id, values in sorted(
            teams.items(),
            key=lambda item: (item[1]["simplified_xg"], item[1]["big_chances"]),
            reverse=True,
        )
    ]


def _ranked_player_creation(players: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return _ranked(players, "chance_creation_score")


def _ranked(metrics: dict[str, dict[str, Any]], field: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        {"player_id": entity_id, **values}
        for entity_id, values in metrics.items()
        if values.get(field)
    ]
    return sorted(rows, key=lambda item: (item[field], item.get("simplified_xa", 0.0)), reverse=True)[:limit]


def _biggest_chances(shots: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return sorted(
        (_shot_timeline_item(shot) for shot in shots),
        key=lambda item: (item["simplified_xg"], item["is_big_chance"]),
        reverse=True,
    )[:limit]


def _high_value_chance_timeline(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _shot_timeline_item(shot)
        for shot in sorted(shots, key=lambda item: (item.get("minute") is None, item.get("minute") or 0, item["event_id"]))
        if bool(_qualifier(shot).get("is_big_chance")) or float(_qualifier(shot).get("simplified_xg", 0.0)) >= 0.20
    ]


def _shot_timeline_item(shot: dict[str, Any]) -> dict[str, Any]:
    qualifier = _qualifier(shot)
    return {
        "event_id": shot["event_id"],
        "minute": shot.get("minute"),
        "team_id": shot.get("team_id"),
        "player_id": shot.get("player_id"),
        "simplified_xg": qualifier.get("simplified_xg", 0.0),
        "shot_class": qualifier.get("shot_class", "unknown"),
        "is_big_chance": bool(qualifier.get("is_big_chance")),
        "proxy": True,
        "not_official_xg": True,
    }


def _data_gaps(events: list[dict[str, Any]], shots: list[dict[str, Any]], unlinked_passes: int) -> list[str]:
    gaps: list[str] = []
    if not events:
        gaps.append("no_post_match_timeline_or_commentary_events")
    if not shots:
        gaps.append("no_shot_events")
    if unlinked_passes:
        gaps.append("pass_to_shot_events_without_linked_shot")
    if any(not event.get("player_id") for event in events if event["event_type"] in {"shot", "pass_to_shot"}):
        gaps.append("missing_player_id")
    if any(classify_shot(event) == "unknown" for event in shots):
        gaps.append("unknown_shot_location_or_method")
    if any(not event.get("team_id") for event in events):
        gaps.append("missing_team_id")
    return gaps


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    signatures: dict[tuple[Any, ...], int] = {}
    deduped: list[dict[str, Any]] = []
    for event in events:
        event_id = str(event["event_id"])
        if event_id in seen:
            continue
        signature = _merge_signature(event)
        if signature in signatures:
            existing = deduped[signatures[signature]]
            if _is_commentary_source(event) or _is_commentary_source(existing):
                existing["qualifier"] = {**_qualifier(existing), **_qualifier(event)}
                existing["confidence"] = max(float(existing.get("confidence", 0.0)), float(event.get("confidence", 0.0)))
                if existing.get("source") != event.get("source"):
                    existing["source"] = f"{existing.get('source')}+{event.get('source')}"
                seen.add(event_id)
                continue
        seen.add(event_id)
        signatures[signature] = len(deduped)
        deduped.append(event)
    return deduped


def _merge_signature(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event.get("match_id"),
        event.get("event_type"),
        event.get("minute"),
        event.get("team_id"),
        event.get("player_id"),
    )


def _is_commentary_source(event: dict[str, Any]) -> bool:
    return "commentary" in str(event.get("source") or "")


def _qualifier(event: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    qualifier = event.get("qualifier")
    return qualifier if isinstance(qualifier, dict) else {}


def _first_value(event: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = event.get(key)
        if value is not None:
            return value
    return None


def _minute(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(float(str(value).replace("+", "."))))
    except ValueError:
        return None


def _minute_distance(left: Any, right: Any) -> int:
    if left is None or right is None:
        return 999
    return abs(int(left) - int(right))


def _confidence(value: Any) -> float:
    if value is None:
        return 0.8
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.8


def _normalize_token(value: Any) -> str:
    if isinstance(value, (dict, list, set, tuple)):
        return ""
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _entity_id(value: Any, fallback: str) -> str:
    return str(value) if value is not None and value != "" else fallback
