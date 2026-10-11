"""Grouping a week's games into slates: sets of games that kick off together.

Slates are read off the real schedule, not assumed from the day of the week, so
Thanksgiving's three games, a Christmas Wednesday, a Saturday tripleheader or a
London morning game each become their own slate without special handling.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from .schedule import ET

# Sunday's 4:05 and 4:25 games are one slate; 1:00 and 4:05 are two.
SLATE_WINDOW = timedelta(minutes=90)

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class HasKickoff(Protocol):
    espn_id: str
    kickoff: datetime


@dataclass(frozen=True)
class Slate:
    key: str  # first kickoff in UTC; identifies the slate once its update is posted
    name: str  # "Sunday early", "Thanksgiving night", "Black Friday"
    start: datetime
    game_ids: tuple[str, ...]


def thanksgiving(year: int) -> date:
    """The fourth Thursday of November."""
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def holiday(day: date) -> str | None:
    turkey = thanksgiving(day.year)
    if day == turkey:
        return "Thanksgiving"
    if day == turkey + timedelta(days=1):
        return "Black Friday"
    return {
        (12, 24): "Christmas Eve",
        (12, 25): "Christmas Day",
        (12, 31): "New Year's Eve",
        (1, 1): "New Year's Day",
    }.get((day.month, day.day))


def _part_of_day(start: datetime) -> str:
    hour = start.astimezone(ET).hour
    if hour < 12:
        return "morning"
    if hour < 15:
        return "early"
    if hour < 19:
        return "late"
    return "night"


def build_slates(games: Iterable[HasKickoff]) -> list[Slate]:
    """Slates in kickoff order. A game joins a slate if it starts within 90 minutes of the slate's first game.

    A game whose kickoff time has not been announced belongs to no slate yet.
    """
    timed = [game for game in games if getattr(game, "time_set", True)]
    groups: list[list[HasKickoff]] = []
    for game in sorted(timed, key=lambda item: (item.kickoff, item.espn_id)):
        if groups and game.kickoff - groups[-1][0].kickoff <= SLATE_WINDOW:
            groups[-1].append(game)
        else:
            groups.append([game])

    per_day: dict[date, int] = {}
    for group in groups:
        day = group[0].kickoff.astimezone(ET).date()
        per_day[day] = per_day.get(day, 0) + 1

    slates = []
    for group in groups:
        start = group[0].kickoff
        day = start.astimezone(ET).date()
        special = holiday(day)
        if special and per_day[day] == 1:
            name = special
        else:
            name = f"{special or _DAY_NAMES[day.weekday()]} {_part_of_day(start)}"
        key = start.astimezone(timezone.utc).isoformat(timespec="minutes")
        slates.append(Slate(key, name, start, tuple(game.espn_id for game in group)))
    return slates
