"""Deciding whether a leg won, from scores and box-score stats."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .markets import PropMarket, Stats

PENDING = "pending"
WON = "won"
LOST = "lost"
PUSH = "push"  # landed exactly on the line; the leg drops out of the parlay
VOID = "void"  # the bet never had action (player did not play); the leg drops out
REVIEW = "review"  # the bot could not decide; the commissioner settles it with /grade

SETTLED = frozenset({WON, LOST, PUSH, VOID})
DROPPED = frozenset({PUSH, VOID})


@dataclass(frozen=True)
class Grade:
    status: str
    value: float | None = None
    note: str | None = None


def _over_under(value: float, side: str, line: float) -> str:
    if value == line:
        return PUSH
    went_over = value > line
    return WON if went_over == (side == "over") else LOST


def grade_team_bet(
    market: str, side: str, line: float | None, home_score: int, away_score: int, final: bool
) -> Grade:
    """Grade a moneyline, spread or game total.

    For h2h and spreads, side is 'home' or 'away' and line is that team's spread.
    For totals, side is 'over' or 'under'.
    """
    if market == "totals":
        total = home_score + away_score
        if final:
            return Grade(_over_under(total, side, line), total)
        # Points only go up, so a total that is already over is decided.
        if total > line:
            return Grade(WON if side == "over" else LOST, total)
        return Grade(PENDING, total)

    mine, theirs = (home_score, away_score) if side == "home" else (away_score, home_score)
    if not final:
        return Grade(PENDING, mine - theirs)
    spread = line if market == "spreads" and line is not None else 0.0
    margin = mine - theirs + spread
    if margin == 0:
        return Grade(PUSH, mine - theirs)
    return Grade(WON if margin > 0 else LOST, mine - theirs)


def grade_prop(
    prop: PropMarket,
    side: str,
    line: float | None,
    stats: Stats | None,
    final: bool,
    played: bool | None = None,
) -> Grade:
    """Grade a player prop.

    stats is the player's box-score line, or None if they have none. played is
    only consulted in that case, at the final whistle: True means they were on
    the field and recorded nothing, False means they did not play, and None
    means it could not be determined.
    """
    if stats is None:
        if not final:
            return Grade(PENDING)
        if played is None:
            return Grade(REVIEW, note="No stats found for this player. Settle with /grade.")
        if not played:
            return Grade(VOID, note="Did not play")
        stats = {}

    value = prop.stat(stats)
    if prop.yes_only:
        if value >= 1:
            return Grade(WON, value)
        return Grade(LOST if final else PENDING, value)

    if final:
        return Grade(_over_under(value, side, line), value)
    if prop.monotonic and value > line:
        return Grade(WON if side == "over" else LOST, value)
    return Grade(PENDING, value)


def parlay_status(statuses: Iterable[str]) -> str:
    """The state of the whole slip: one miss kills it; it wins when every leg is settled."""
    seen = list(statuses)
    if LOST in seen:
        return LOST
    if not seen or any(status not in SETTLED for status in seen):
        return PENDING
    return WON if WON in seen else VOID
