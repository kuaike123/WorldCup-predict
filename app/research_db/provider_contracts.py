from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


ProviderResultStatus = Literal["ok", "partial", "missing", "failed", "unsupported"]
ProviderData = list[dict[str, Any]]
ProviderFetcher = Callable[[str], "ProviderResult"]


@dataclass(frozen=True)
class ProviderResult:
    """Stable single-entity result returned by every provider implementation."""

    status: ProviderResultStatus
    data: ProviderData = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unsupported(cls, *, provider: str, capability: str) -> "ProviderResult":
        return cls(
            status="unsupported",
            diagnostics={
                "provider": provider,
                "capability": capability,
                "reason": "capability_not_supported",
            },
        )

    @classmethod
    def missing(cls, *, provider: str, capability: str, reason: str) -> "ProviderResult":
        return cls(
            status="missing",
            diagnostics={
                "provider": provider,
                "capability": capability,
                "reason": reason,
            },
        )


class BaseProvider(ABC):
    """Minimal public provider contract used by plugin integrations.

    Providers must return explicit results for all three capabilities. A provider
    that does not support a capability returns ``ProviderResult.unsupported``;
    unsupported operations must never fail silently.
    """

    name: str

    @abstractmethod
    def get_recent_results(self, team_id: str) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def get_player_form(self, player_id: str) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def get_odds(self, match_id: str) -> ProviderResult:
        raise NotImplementedError


class CrawlerProvider(BaseProvider):
    """Adapter contract for an optional user-installed crawler runtime.

    The public package intentionally does not bundle a crawler. Callers inject
    the supported entity fetchers from their crawler installation. Missing
    fetchers produce explicit ``missing`` results instead of import-time errors.
    """

    name = "crawler"

    def __init__(
        self,
        *,
        recent_results_fetcher: ProviderFetcher | None = None,
        player_form_fetcher: ProviderFetcher | None = None,
        odds_fetcher: ProviderFetcher | None = None,
    ) -> None:
        self._recent_results_fetcher = recent_results_fetcher
        self._player_form_fetcher = player_form_fetcher
        self._odds_fetcher = odds_fetcher

    def get_recent_results(self, team_id: str) -> ProviderResult:
        return self._call(
            self._recent_results_fetcher,
            entity_id=team_id,
            capability="recent_results",
        )

    def get_player_form(self, player_id: str) -> ProviderResult:
        return self._call(
            self._player_form_fetcher,
            entity_id=player_id,
            capability="player_form",
        )

    def get_odds(self, match_id: str) -> ProviderResult:
        return self._call(
            self._odds_fetcher,
            entity_id=match_id,
            capability="odds",
        )

    def _call(
        self,
        fetcher: ProviderFetcher | None,
        *,
        entity_id: str,
        capability: str,
    ) -> ProviderResult:
        if fetcher is None:
            return ProviderResult.missing(
                provider=self.name,
                capability=capability,
                reason="crawler_fetcher_not_configured",
            )
        result = fetcher(entity_id)
        if not isinstance(result, ProviderResult):
            raise TypeError(f"crawler_fetcher_must_return_provider_result:{capability}")
        return result
