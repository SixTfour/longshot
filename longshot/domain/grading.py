"""Deciding whether a leg won, from scores and box-score stats."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .injuries import INJURED, LEFT_INJURED, NOT_INJURED, RETURNED
from .markets import PropMarket, Stats

PENDING = "pending"
WON = "won"
LOST = "lost"
PUSH = "push"  # landed exactly on the line; the leg drops out of the parlay
VOID = "void"  # the bet never had action (player did not play, or got hurt and missed); the leg drops out
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


INJURED_NOTE = "Injured during the game"
HOLD_NOTE = "Missed, but the player was hurt during the game. Waiting on an /injury ruling."


def apply_injury(grade: Grade, side: str, espn: str | None, ruling: str | None, hold_over: bool) -> Grade:
    """The injury exemption, for a player prop.

    A miss on an over or an anytime TD is void if the player got hurt: by the
    commissioner's ruling, or else if ESPN shows they left injured and never
    came back. If ESPN shows they were hurt but came back, the miss is held
    (left pending) until hold_over, so the commissioner can rule. A hit stands,
    and an under is never voided: going over is not something an injury causes.
    """
    if grade.status != LOST or side == "under" or ruling == NOT_INJURED:
        return grade
    if ruling == INJURED or espn == LEFT_INJURED:
        return Grade(VOID, grade.value, INJURED_NOTE)
    if espn == RETURNED and not hold_over:
        return Grade(PENDING, grade.value, HOLD_NOTE)
    return grade


def parlay_status(statuses: Iterable[str]) -> str:
    """The state of the whole slip: one miss kills it; it wins when every leg is settled."""
    seen = list(statuses)
    if LOST in seen:
        return LOST
    if not seen or any(status not in SETTLED for status in seen):
        return PENDING
    return WON if WON in seen else VOID
