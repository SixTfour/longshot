"""Lines and prices from The Odds API, fetched as rarely as possible.

Every paid call goes through a cache and a credit guard. A lookup that is
already running is shared, so two members opening the same list cost one call.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from functools import partial
from typing import Any

import aiohttp

from .clients.espn import Game
from .clients.odds import OddsApiError, OddsClient
from .db import Database, parse_iso, utcnow
from .domain import lines as line_rules
from .domain.lines import Line, bookmaker_markets
from .domain.markets import TEAM_ALTERNATES, PropMarket
from .domain.names import normalize
from .settings import Settings

log = logging.getLogger(__name__)

LOW_WATER = 50  # stop spending below this many credits
EVENTS_TTL = timedelta(hours=6)
EMPTY_TTL = timedelta(minutes=30)  # retry sooner when the feed had nothing
STALE_READING = timedelta(hours=1)
TRUST_READING = timedelta(hours=24)


class NoCredits(RuntimeError):
    """The Odds API quota is nearly used up, so no new list is fetched."""


class LineBook:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        db: Database,
        settings: Settings,
        on_low: Callable[[int], Awaitable[None]] | None = None,
    ):
        self._odds = OddsClient(session, api_key, on_usage=self._record_usage)
        self._db = db
        self._settings = settings
        self._on_low = on_low
        self._inflight: dict[str, asyncio.Task] = {}

    # --- credits ----------------------------------------------------------------

    async def _record_usage(self, remaining: int | None, used: int | None) -> None:
        await self._db.set_state("odds_remaining", remaining)
        await self._db.set_state("odds_checked_at", utcnow().isoformat())
        if used is not None:
            await self._db.set_state("odds_used", used)
        warned = await self._db.get_state("odds_low_warned") == "1"
        if remaining < LOW_WATER and not warned:
            await self._db.set_state("odds_low_warned", "1")
            if self._on_low:
                await self._on_low(remaining)
        elif remaining >= LOW_WATER and warned:
            await self._db.set_state("odds_low_warned", "0")

    async def usage(self) -> tuple[int | None, int | None]:
        """(credits remaining, credits used) as of the last call, or None if never called."""
        remaining = await self._db.get_state("odds_remaining")
        used = await self._db.get_state("odds_used")
        return (int(remaining) if remaining else None, int(used) if used else None)

    async def _check_budget(self) -> None:
        remaining, _ = await self.usage()
        if remaining is None or remaining >= LOW_WATER:
            return
        checked = await self._db.get_state("odds_checked_at")
        age = utcnow() - parse_iso(checked) if checked else TRUST_READING
        if age >= STALE_READING:
            # The quota may have reset since the low reading. The events call is free.
            try:
                await self._odds.events()
            except OddsApiError:
                pass
            remaining, _ = await self.usage()
            if age >= TRUST_READING and (await self._db.get_state("odds_checked_at")) == checked:
                return  # no fresh reading to be had; do not stay locked out for ever
        if remaining is not None and remaining < LOW_WATER:
            raise NoCredits(f"Only {remaining} Odds API credits are left this month.")

    # --- cache ------------------------------------------------------------------

    async def _load(self, key: str, fetch: Callable[[], Awaitable[Any]], ttl: timedelta, *, paid: bool) -> Any:
        cached = await self._db.cache_get(key)
        if cached is not None:
            payload, fetched_at = cached
            if utcnow() - fetched_at < (ttl if payload else min(ttl, EMPTY_TTL)):
                return payload

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(key, fetch, paid))
            self._inflight[key] = task
            task.add_done_callback(partial(self._finished, key))
        try:
            # Shielded so that a caller giving up (Discord's 3-second autocomplete
            # limit) does not abandon a call that has already been paid for.
            return await asyncio.shield(task)
        except (NoCredits, OddsApiError):
            if cached is not None and cached[0]:
                return cached[0]  # an old list beats none
            raise

    async def _fetch(self, key: str, fetch: Callable[[], Awaitable[Any]], paid: bool) -> Any:
        if paid:
            await self._check_budget()
        payload = await fetch()
        await self._db.cache_put(key, payload)
        return payload

    def _finished(self, key: str, task: asyncio.Task) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            log.warning("Odds lookup %s failed: %s", key, task.exception())

    async def _ttl(self) -> timedelta:
        return timedelta(hours=await self._settings.cache_hours())

    # --- lookups ----------------------------------------------------------------

    async def _events(self) -> list[dict[str, Any]]:
        async def fetch():
            return [
                {key: event[key] for key in ("id", "home_team", "away_team", "commence_time")}
                for event in await self._odds.events()
            ]

        return await self._load("odds:events", fetch, EVENTS_TTL, paid=False)

    async def event_id(self, game: Game) -> str | None:
        """The odds feed's id for an ESPN game, matched on team names and date."""
        home, away = normalize(game.home.name), normalize(game.away.name)
        for event in await self._events():
            if normalize(event["home_team"]) != home or normalize(event["away_team"]) != away:
                continue
            commence = parse_iso(event["commence_time"].replace("Z", "+00:00"))
            if abs(commence - game.kickoff) < timedelta(hours=36):
                return event["id"]
        return None

    async def _main(self, book: str) -> dict[str, dict]:
        async def fetch():
            return {
                event["id"]: markets
                for event in await self._odds.main_markets(book)
                if (markets := bookmaker_markets(event, book))
            }

        return await self._load(f"odds:main:{book}", fetch, await self._ttl(), paid=True)

    async def _event(self, event_id: str, markets: list[str], book: str) -> dict[str, list]:
        async def fetch():
            return bookmaker_markets(await self._odds.event_markets(event_id, markets, book), book)

        return await self._load(f"odds:event:{event_id}:{book}:{markets[0]}", fetch, await self._ttl(), paid=True)

    async def team_lines(self, game: Game, team_abbr: str, bet: str) -> list[Line]:
        """Moneyline, spread or total for a team's game, alternates included."""
        team = game.team(team_abbr)
        event_id = await self.event_id(game)
        if team is None or event_id is None:
            return []
        book = await self._settings.sportsbook()
        markets = dict((await self._main(book)).get(event_id, {}))

        alternate = TEAM_ALTERNATES.get(bet)
        if alternate:
            try:
                markets.update(await self._event(event_id, [alternate], book))
            except (NoCredits, OddsApiError):
                if bet not in markets:
                    raise
        return line_rules.team_lines(
            markets, bet, team_name=team.name, team_abbr=team.abbr, is_home=team.abbr == game.home.abbr
        )

    async def player_lines(self, game: Game, player_name: str, prop: PropMarket) -> list[Line]:
        """Main and alternate lines for one player's prop."""
        event_id = await self.event_id(game)
        if event_id is None:
            return []
        book = await self._settings.sportsbook()
        keys = [prop.key] + ([prop.alternate_key] if prop.alternate_key else [])
        return line_rules.player_lines(await self._event(event_id, keys, book), prop, player_name)
