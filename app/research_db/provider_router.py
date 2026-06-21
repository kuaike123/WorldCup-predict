from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


RESEARCH_PROVIDERS = {"auto", "sportradar_soccer", "crawler", "skip"}
ODDS_PROVIDERS = {"auto", "the_odds_api", "crawler", "skip"}
LEGACY_SOURCE_MODES = {"auto", "api", "crawler"}


@dataclass(frozen=True)
class ProviderSelection:
    configured: str
    selected: str
    fallback_used: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "selected": self.selected,
            "fallback_used": self.fallback_used,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProviderRoute:
    research: ProviderSelection
    odds: ProviderSelection
    legacy_source_mode: str

    @property
    def research_provider(self) -> str:
        return self.research.selected

    @property
    def odds_provider(self) -> str:
        return self.odds.selected

    def as_dict(self) -> dict[str, Any]:
        return {
            "research_provider": self.research_provider,
            "odds_provider": self.odds_provider,
            "research": self.research.as_dict(),
            "odds": self.odds.as_dict(),
            "legacy_source_mode": self.legacy_source_mode,
            "legacy_source_mode_affects_selection": False,
        }


def resolve_provider_route(
    *,
    settings: Any,
    skill_scripts_dir: Path,
    legacy_source_mode: str | None = None,
) -> ProviderRoute:
    """Resolve research and odds providers independently from configuration.

    ``legacy_source_mode`` is accepted only for CLI compatibility and is
    intentionally excluded from provider selection.
    """

    normalized_legacy_mode = _normalize_legacy_source_mode(legacy_source_mode)
    crawler_enabled = bool(getattr(settings, "enable_crawler", True))
    research_crawler_available = crawler_enabled and _crawler_research_available(skill_scripts_dir)
    odds_crawler_available = crawler_enabled and _crawler_odds_available(skill_scripts_dir)

    configured_research = normalize_research_provider(
        str(getattr(settings, "data_source_research_provider", "auto") or "auto")
    )
    configured_odds = normalize_odds_provider(
        str(getattr(settings, "data_source_odds_provider", "auto") or "auto")
    )

    research = _resolve_research_provider(
        configured=configured_research,
        has_sportradar_key=bool(
            str(getattr(settings, "sportradar_soccer_api_key", "") or "").strip()
        ),
        crawler_available=research_crawler_available,
    )
    odds = _resolve_odds_provider(
        configured=configured_odds,
        has_odds_key=bool(str(getattr(settings, "the_odds_api_key", "") or "").strip()),
        crawler_available=odds_crawler_available,
    )
    return ProviderRoute(
        research=research,
        odds=odds,
        legacy_source_mode=normalized_legacy_mode,
    )


def normalize_research_provider(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "": "auto",
        "default": "auto",
        "sportradar": "sportradar_soccer",
        "external_crawler": "crawler",
        "none": "skip",
        "disabled": "skip",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in RESEARCH_PROVIDERS:
        raise ValueError(f"unsupported_research_provider:{value}")
    return normalized


def normalize_odds_provider(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "": "auto",
        "default": "auto",
        "odds_api": "the_odds_api",
        "external_crawler": "crawler",
        "none": "skip",
        "disabled": "skip",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ODDS_PROVIDERS:
        raise ValueError(f"unsupported_odds_provider:{value}")
    return normalized


def _resolve_research_provider(
    *,
    configured: str,
    has_sportradar_key: bool,
    crawler_available: bool,
) -> ProviderSelection:
    if configured == "skip":
        return ProviderSelection(configured, "skip", False, "research_disabled")
    if configured == "sportradar_soccer":
        if has_sportradar_key:
            return ProviderSelection(configured, "sportradar_soccer", False, "configured_provider_available")
        if crawler_available:
            return ProviderSelection(configured, "crawler", True, "sportradar_key_missing_crawler_fallback")
        return ProviderSelection(configured, "skip", False, "sportradar_key_missing")
    if configured == "crawler":
        if crawler_available:
            return ProviderSelection(configured, "crawler", False, "configured_provider_available")
        return ProviderSelection(configured, "skip", False, "crawler_unavailable")
    if has_sportradar_key:
        return ProviderSelection(configured, "sportradar_soccer", False, "auto_selected_sportradar")
    if crawler_available:
        return ProviderSelection(configured, "crawler", False, "auto_selected_crawler")
    return ProviderSelection(configured, "skip", False, "no_research_provider_available")


def _resolve_odds_provider(
    *,
    configured: str,
    has_odds_key: bool,
    crawler_available: bool,
) -> ProviderSelection:
    if configured == "skip":
        return ProviderSelection(configured, "skip", False, "odds_disabled")
    if configured == "the_odds_api":
        if has_odds_key:
            return ProviderSelection(configured, "the_odds_api", False, "configured_provider_available")
        if crawler_available:
            return ProviderSelection(configured, "crawler", True, "odds_api_key_missing_crawler_fallback")
        return ProviderSelection(configured, "skip", False, "odds_api_key_missing")
    if configured == "crawler":
        if crawler_available:
            return ProviderSelection(configured, "crawler", False, "configured_provider_available")
        return ProviderSelection(configured, "skip", False, "crawler_unavailable")
    if has_odds_key:
        return ProviderSelection(configured, "the_odds_api", False, "auto_selected_the_odds_api")
    if crawler_available:
        return ProviderSelection(configured, "crawler", False, "auto_selected_crawler")
    return ProviderSelection(configured, "skip", False, "no_odds_provider_available")


def _normalize_legacy_source_mode(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in LEGACY_SOURCE_MODES:
        raise ValueError("source_mode_must_be_auto_api_or_crawler")
    return normalized


def _crawler_research_available(skill_scripts_dir: Path) -> bool:
    return skill_scripts_dir.is_dir() and (skill_scripts_dir / "whoscored_workflow.py").is_file()


def _crawler_odds_available(skill_scripts_dir: Path) -> bool:
    return skill_scripts_dir.is_dir() and (skill_scripts_dir / "soccerway_odds.py").is_file()
