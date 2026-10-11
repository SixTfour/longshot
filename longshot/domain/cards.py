"""Which parlays a week has.

Every week has one main parlay, paid for by the previous week's lowest scorer.
Thanksgiving week has two: a bonus parlay on the Wednesday, Thursday and Friday
games, paid for by the commissioner, and the main parlay on the games after.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TypeVar

from .schedule import ET
from .slates import HasKickoff, thanksgiving

MAIN = "main"
BONUS = "bonus"

G = TypeVar("G", bound=HasKickoff)


def split_week(games: Iterable[G]) -> dict[str, list[G]]:
    """The week's games per parlay, in the order the parlays are played."""
    games = list(games)
    timed = [game for game in games if getattr(game, "time_set", True)]
    days = {game.kickoff.astimezone(ET).date() for game in timed}
    turkey = next((day for day in days if day == thanksgiving(day.year)), None)
    if turkey is None:
        return {MAIN: games}

    black_friday = turkey + timedelta(days=1)
    bonus = [game for game in timed if game.kickoff.astimezone(ET).date() <= black_friday]
    held = {game.espn_id for game in bonus}
    return {BONUS: bonus, MAIN: [game for game in games if game.espn_id not in held]}


def card_title(week: int, card: str) -> str:
    """'Week 12' or 'Thanksgiving bonus'; read naturally before 'parlay' or 'picks'."""
    return "Thanksgiving bonus" if card == BONUS else f"Week {week}"


def card_scope(card: str, split: bool) -> str:
    """Which games a parlay covers, for the announcement."""
    if card == BONUS:
        return "Wednesday, Thursday and Friday games"
    return "Sunday and Monday games" if split else "this week's games"
