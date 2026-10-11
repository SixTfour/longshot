"""The Odds API v4. Every paid call costs credits, so callers go through LineService's cache.

The key travels as a URL parameter. Nothing here logs or raises a URL without
passing it through redact() first.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import aiohttp

from ..redact import redact

BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"

UsageCallback = Callable[[int | None, int | None], Awaitable[None]]


class OddsApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(redact(message))
        self.status = status


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


class OddsClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, on_usage: UsageCallback | None = None):
        self._session = session
        self._api_key = api_key
        self._on_usage = on_usage

    async def _get(self, path: str, **params: str) -> Any:
        params["apiKey"] = self._api_key
        try:
            async with self._session.get(f"{BASE}{path}", params=params) as response:
                remaining = _int(response.headers.get("x-requests-remaining"))
                used = _int(response.headers.get("x-requests-used"))
                if self._on_usage and remaining is not None:
                    await self._on_usage(remaining, used)
                if response.status != 200:
                    try:
                        detail = (await response.json()).get("message", "")
                    except (aiohttp.ContentTypeError, ValueError, AttributeError):
                        detail = ""
                    raise OddsApiError(
                        f"The Odds API returned HTTP {response.status}. {detail}".strip(), response.status
                    )
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as error:
            raise OddsApiError(f"Could not reach The Odds API: {error or type(error).__name__}") from None

    async def events(self) -> list[dict[str, Any]]:
        """Upcoming games with their ids. Free: costs no credits."""
        return await self._get("/events")

    async def main_markets(self, bookmaker: str) -> list[dict[str, Any]]:
        """Moneyline, spread and total for every upcoming game. Costs 3 credits."""
        return await self._get(
            "/odds", bookmakers=bookmaker, markets="h2h,spreads,totals", oddsFormat="american"
        )

    async def event_markets(self, event_id: str, markets: Sequence[str], bookmaker: str) -> dict[str, Any]:
        """Any markets for one game. Costs 1 credit per market that comes back with data."""
        return await self._get(
            f"/events/{event_id}/odds", bookmakers=bookmaker, markets=",".join(markets), oddsFormat="american"
        )
