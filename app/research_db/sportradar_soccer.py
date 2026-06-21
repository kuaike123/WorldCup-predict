from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings

from .provider_contracts import BaseProvider, ProviderResult


@dataclass(frozen=True)
class SportradarError:
    code: str
    message: str
    status_code: int | None = None


@dataclass(frozen=True)
class SportradarResponse:
    data: Any = None
    error: SportradarError | None = None
    meta: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class SportradarSoccerProvider(BaseProvider):
    name = "sportradar_soccer"

    def __init__(
        self,
        settings: Settings,
        *,
        adapter: "SportradarSoccerAdapter | None" = None,
    ) -> None:
        self.adapter = adapter or SportradarSoccerAdapter(settings)

    def get_team_profile(self, team_id: str) -> ProviderResult:
        return _provider_result_from_response(
            self.adapter.fetch_competitor_profile(team_id),
            provider=self.name,
            capability="team_profile",
            entity_id=team_id,
        )

    def get_recent_results(self, team_id: str) -> ProviderResult:
        return _provider_result_from_response(
            self.adapter.fetch_competitor_schedules(team_id),
            provider=self.name,
            capability="recent_results",
            entity_id=team_id,
        )

    def get_player_form(self, player_id: str) -> ProviderResult:
        return _provider_result_from_response(
            self.adapter.fetch_player_summaries(player_id),
            provider=self.name,
            capability="player_form",
            entity_id=player_id,
        )

    def get_odds(self, match_id: str) -> ProviderResult:
        return ProviderResult.unsupported(provider=self.name, capability="odds")


class SportradarSoccerAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_competitor_profile(self, competitor_id: str) -> SportradarResponse:
        return self._get(f"competitors/{_path_id(competitor_id)}/profile")

    def fetch_competitor_schedules(self, competitor_id: str) -> SportradarResponse:
        return self._get(f"competitors/{_path_id(competitor_id)}/schedules")

    def fetch_player_summaries(self, player_id: str) -> SportradarResponse:
        return self._get(f"players/{_path_id(player_id)}/summaries")

    def _get(self, path: str) -> SportradarResponse:
        if not self.settings.sportradar_soccer_api_key:
            return SportradarResponse(
                error=SportradarError("missing_config", "SPORTRADAR_SOCCER_API_KEY is not configured")
            )

        url = (
            f"{self.settings.sportradar_soccer_base_url.rstrip('/')}"
            f"/{self.settings.sportradar_soccer_access_level}"
            f"/v4/{self.settings.sportradar_soccer_language}/{path}.json"
        )
        headers = {
            "accept": "application/json",
            "x-api-key": self.settings.sportradar_soccer_api_key,
        }
        try:
            with httpx.Client(timeout=self.settings.sportradar_soccer_timeout_seconds, trust_env=False) as client:
                response = client.get(url, headers=headers)
        except httpx.TimeoutException:
            return SportradarResponse(error=SportradarError("timeout", "Sportradar request timed out"))
        except httpx.HTTPError:
            return SportradarResponse(error=SportradarError("api_error", "Sportradar request failed"))

        status_code = int(getattr(response, "status_code", 0))
        if status_code >= 400:
            return SportradarResponse(error=_error_from_status(status_code))

        try:
            payload = response.json()
        except ValueError:
            return SportradarResponse(error=SportradarError("api_error", "Sportradar response is not valid JSON"))
        return SportradarResponse(data=payload, meta=_response_meta(response))


def _provider_result_from_response(
    response: SportradarResponse,
    *,
    provider: str,
    capability: str,
    entity_id: str,
) -> ProviderResult:
    if response.error is not None:
        status = "missing" if response.error.code in {"missing_config", "not_found"} else "failed"
        return ProviderResult(
            status=status,
            diagnostics={
                "provider": provider,
                "capability": capability,
                "entity_id": entity_id,
                "error_code": response.error.code,
                "message": response.error.message,
                "status_code": response.error.status_code,
            },
        )
    data = response.data
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    return ProviderResult(
        status="ok" if rows else "missing",
        data=[item for item in rows if isinstance(item, dict)],
        diagnostics={
            "provider": provider,
            "capability": capability,
            "entity_id": entity_id,
            "meta": response.meta or {},
        },
    )


def _path_id(value: str) -> str:
    return quote(value, safe="")


def _error_from_status(status_code: int) -> SportradarError:
    if status_code in {401, 403}:
        return SportradarError("unauthorized", "Sportradar request is unauthorized", status_code)
    if status_code == 404:
        return SportradarError("not_found", "Sportradar resource was not found", status_code)
    if status_code == 429:
        return SportradarError("rate_limited", "Sportradar rate limit reached", status_code)
    if status_code >= 500:
        return SportradarError("temporarily_unavailable", "Sportradar service is temporarily unavailable", status_code)
    return SportradarError("api_error", f"Sportradar returned HTTP {status_code}", status_code)


def _response_meta(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    return {
        "status_code": str(getattr(response, "status_code", "")),
        "rate_limit": str(headers.get("x-ratelimit-limit", "")),
        "rate_remaining": str(headers.get("x-ratelimit-remaining", "")),
        "rate_reset": str(headers.get("x-ratelimit-reset", "")),
    }
