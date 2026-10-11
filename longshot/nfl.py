"""The NFL schedule and rosters, from ESPN, with caching.

Also answers "which week's parlays are live right now", which ESPN's own week
pointer cannot: ESPN rolls over on Wednesday, the parlay cycle on Tuesday.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clients.espn import REGULAR_SEASON, Athlete, EspnClient, EspnError, Game, Scoreboard
from .db import Database, utcnow
from .domain.cards import MAIN, card_title, split_week
from .domain.schedule import WeekTimes, week_times
from .settings import Settings

log = logging.getLogger(__name__)

LAST_WEEK = 18
SCHEDULE_TTL = timedelta(minutes=30)
ROSTER_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class Parlay:
    """One parlay in one week: its games and its times."""

    season: int
    week: int
    card: str  # 'main', or 'bonus' for the Thanksgiving extra
    games: list[Game]
    times: WeekTimes

    @property
    def key(self) -> tuple[int, int, str]:
        return self.season, self.week, self.card

    @property
    def title(self) -> str:
        return card_title(self.week, self.card)

    @property
    def loser_pays(self) -> bool:
        """The main parlay is funded by the lowest scorer; the bonus one by the commissioner."""
        return self.card == MAIN

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


@dataclass(frozen=True)
class WeekPlan:
    """A week and its parlays: one in most weeks, two in Thanksgiving week."""

    season: int
    week: int
    games: list[Game]
    announce_at: datetime
    parlays: list[Parlay]  # in the order they are played

    @property
    def split(self) -> bool:
        return len(self.parlays) > 1

    def parlay(self, card: str) -> Parlay | None:
        return next((parlay for parlay in self.parlays if parlay.card == card), None)


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

    async def plan(self, season: int, week: int) -> WeekPlan | None:
        games = await self.games(season, week)
        if not games:
            return None
        first_kickoff = min((game.kickoff for game in games if game.time_set), default=games[0].kickoff)
        announce, reminders = await self._settings.announce(), await self._settings.reminders()
        parlays = []
        for card, card_games in split_week(games).items():
            times = week_times(first_kickoff, announce, await self._settings.deadline(card), reminders)
            parlays.append(Parlay(season, week, card, card_games, times))
        return WeekPlan(season, week, games, parlays[0].times.announce_at, parlays)

    async def parlay(self, season: int, week: int, card: str) -> Parlay | None:
        plan = await self.plan(season, week)
        return plan.parlay(card) if plan else None

    async def active(self, now: datetime) -> WeekPlan | None:
        """The latest regular-season week whose announcement time has passed."""
        pointer = await self._pointer()
        if pointer.season_type != REGULAR_SEASON:
            return None
        best = None
        for week in (pointer.week, pointer.week + 1):
            if not 1 <= week <= LAST_WEEK:
                continue
            plan = await self.plan(pointer.season, week)
            if plan and plan.announce_at <= now:
                best = plan
        return best

    # --- rosters ----------------------------------------------------------------

    def athletes(self, games: list[Game]) -> list[Athlete]:
        """Players on the given games' teams, from memory. Call warm() to load them."""
        found: list[Athlete] = []
        for game in games:
            for team in (game.home, game.away):
                found.extend(self._rosters.get(team.abbr, ([], None))[0])
        return found

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
