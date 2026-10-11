"""When the fantasy season runs, read from the Sleeper league itself.

Longshot only operates during the fantasy season: from the league's first
scoring week to the last week of its playoffs.

It also decides who pays for each week's parlay. Normally that is the lowest
scorer of the week before. The commissioner pays in the first week, when no
fantasy week has been played yet, and in every playoff week.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Sleeper's league statuses before any game counts.
_NOT_STARTED = {"pre_draft", "drafting"}
_COMPLETE = "complete"
# Used when a league does not say when its playoffs start: no end is assumed,
# and the NFL regular season is what stops the bot.
_OPEN_ENDED = 99


@dataclass(frozen=True)
class FantasySeason:
    season: int | None  # the year the league belongs to, if it says
    status: str  # Sleeper's: pre_draft, drafting, in_season or complete
    start_week: int
    last_week: int  # the final week of the fantasy playoffs
    playoff_start: int | None = None  # the first playoff week, if the league says

    @property
    def started(self) -> bool:
        return self.status not in _NOT_STARTED

    def runs(self, week: int) -> bool:
        """Whether an NFL week is part of this league's fantasy season."""
        return self.started and self.start_week <= week <= self.last_week

    def commissioner_pays(self, week: int) -> str | None:
        """Why the commissioner, not the lowest scorer, pays for this week's parlay; None if the lowest scorer pays."""
        if week <= self.start_week:
            return "it is the first week of the fantasy season"
        if self.playoff_start is not None and week >= self.playoff_start:
            return "it is a fantasy playoff week"
        return None

    @property
    def commissioner_weeks(self) -> str:
        """The weeks the commissioner pays for, in words."""
        first = f"Week {self.start_week}"
        if self.playoff_start is None or self.playoff_start > self.last_week:
            return first
        if self.playoff_start == self.last_week:
            return f"{first} and Week {self.last_week}"
        return f"{first} and Weeks {self.playoff_start} to {self.last_week}"

    @property
    def span(self) -> str:
        if self.last_week >= _OPEN_ENDED:
            return f"from Week {self.start_week}"
        return f"Weeks {self.start_week} to {self.last_week}"


def league_season(league: Mapping[str, Any]) -> int | None:
    """The season a Sleeper league belongs to, or None if it does not say."""
    try:
        return int(league.get("season"))
    except (TypeError, ValueError):
        return None


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _playoff_weeks(settings: Mapping[str, Any], bracket: Sequence[Mapping[str, Any]] | None) -> int:
    """How many weeks the playoffs take.

    The number of rounds comes from the league's winners bracket, or from the
    number of playoff teams if there is no bracket yet. playoff_round_type is
    Sleeper's setting for longer rounds: 1 is a two-week final, 2 is two weeks
    for every round.
    """
    rounds = max((_number(match.get("r")) for match in bracket or []), default=0)
    if rounds == 0:
        teams = _number(settings.get("playoff_teams"))
        rounds = math.ceil(math.log2(teams)) if teams >= 2 else 0
    kind = _number(settings.get("playoff_round_type"))
    if kind == 1:
        return rounds + 1 if rounds else 0
    if kind == 2:
        return rounds * 2
    return rounds


def fantasy_season(league: Mapping[str, Any], bracket: Sequence[Mapping[str, Any]] | None = None) -> FantasySeason:
    """Read a league's season from Sleeper's league object and its winners bracket."""
    settings = league.get("settings") or {}
    status = str(league.get("status") or "in_season")
    start_week = max(_number(settings.get("start_week"), 1), 1)

    playoffs_start = _number(settings.get("playoff_week_start"))
    last_week = playoffs_start + _playoff_weeks(settings, bracket) - 1 if playoffs_start else _OPEN_ENDED

    # Once Sleeper marks the league complete, it also records the last week it scored.
    scored = _number(settings.get("last_scored_leg"))
    if status == _COMPLETE and scored:
        last_week = scored
    return FantasySeason(league_season(league), status, start_week, last_week, playoffs_start or None)
