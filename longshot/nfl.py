"""The NFL schedule and rosters, from ESPN, with caching.

Also answers "which week's parlay is live right now", which ESPN's own week
pointer cannot: ESPN rolls over on Wednesday, the parlay cycle on Tuesday.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clients.espn import REGULAR_SEASON, Athlete, EspnClient, EspnError, Game, Scoreboard
from .db import Database, utcnow
from .domain.schedule import WeekTimes, week_times
from .settings import Settings

log = logging.getLogger(__name__)

LAST_WEEK = 18
SCHEDULE_TTL = timedelta(minutes=30)
ROSTER_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class WeekContext:
    season: int
    week: int
    games: list[Game]
    times: WeekTimes

    def game(self, espn_id: str) -> Game | None:
        return next((game for game in self.games if game.espn_id == espn_id), None)

    def pickable(self, now: datetime, *, late: bool = False) -> list[Game]:
        """Games a pick can be made on.

        Normally those kicking off after the deadline. A late pick entered by
        the commissioner can use any game that has not started. A game whose
        kickoff time is still to be announced cannot be picked.
        """
        return [
            game
            for game in self.games
            if game.state == "pre"
            and game.time_set
            and game.kickoff > now
            and (late or game.kickoff >= self.times.deadline_at)
        ]


class Nfl:
    def __init__(self, espn: EspnClient, db: Database, settings: Settings):
        self._espn = espn
        self._db = db
        self._settings = settings
        self._current: tuple[Scoreboard, datetime] | None = None
        self._rosters: dict[str, tuple[list[Athlete], datetime]] = {}  # by team abbreviation
        self._warming = asyncio.Lock()

    # --- schedule ---------------------------------------------------------------

    async def games(self, season: int, week: int, *, fresh: bool = False) -> list[Game]:
        """One week's games. fresh=True bypasses the cache, for live scores."""
        key = f"espn:week:{season}:{week}"
        if not fresh:
            cached = await self._db.cache_get(key)
            if cached and utcnow() - cached[1] < SCHEDULE_TTL:
                return [Game.from_json(item) for item in cached[0]]
        board = await self._espn.scoreboard(season, week)
        await self._db.cache_put(key, [game.to_json() for game in board.games])
        return board.games

    async def _pointer(self) -> Scoreboard:
        """ESPN's idea of the current week."""
        if self._current is None or utcnow() - self._current[1] > SCHEDULE_TTL:
            self._current = (await self._espn.scoreboard(), utcnow())
        return self._current[0]

    async def season(self) -> int:
        return (await self._pointer()).season

    async def context(self, season: int, week: int) -> WeekContext | None:
        games = await self.games(season, week)
        if not games:
            return None
        times = week_times(
            min((game.kickoff for game in games if game.time_set), default=games[0].kickoff),
            await self._settings.announce(),
            await self._settings.deadline(),
            await self._settings.reminders(),
        )
        return WeekContext(season, week, games, times)

    async def active(self, now: datetime) -> WeekContext | None:
        """The latest regular-season week whose announcement time has passed."""
        pointer = await self._pointer()
        if pointer.season_type != REGULAR_SEASON:
            return None
        best = None
        for week in (pointer.week, pointer.week + 1):
            if not 1 <= week <= LAST_WEEK:
                continue
            context = await self.context(pointer.season, week)
            if context and context.times.announce_at <= now:
                best = context
        return best

    # --- rosters ----------------------------------------------------------------

    def athletes(self, games: list[Game]) -> list[Athlete]:
        """Players on the given games' teams, from memory. Call warm() to load them."""
        found: list[Athlete] = []
        for game in games:
            for team in (game.home, game.away):
                found.extend(self._rosters.get(team.abbr, ([], None))[0])
        return found

    def athlete(self, athlete_id: str) -> Athlete | None:
        for roster, _ in self._rosters.values():
            for athlete in roster:
                if athlete.id == athlete_id:
                    return athlete
        return None

    def warmed(self, games: list[Game]) -> bool:
        return all(team.abbr in self._rosters for game in games for team in (game.home, game.away))

    async def warm(self, games: list[Game]) -> None:
        """Load rosters for every team in these games, refreshing any older than a day."""
        async with self._warming:
            now = utcnow()
            for game in games:
                for team in (game.home, game.away):
                    held = self._rosters.get(team.abbr)
                    if held and now - held[1] < ROSTER_TTL:
                        continue
                    key = f"espn:roster:{team.abbr}"
                    cached = await self._db.cache_get(key)
                    if cached and now - cached[1] < ROSTER_TTL:
                        self._rosters[team.abbr] = ([Athlete(**item) for item in cached[0]], cached[1])
                        continue
                    try:
                        roster = await self._espn.team_roster(team.id)
                    except EspnError as error:
                        log.warning("Could not load the %s roster: %s", team.abbr, error)
                        continue
                    self._rosters[team.abbr] = (roster, now)
                    await self._db.cache_put(key, [athlete.__dict__ for athlete in roster], now)
