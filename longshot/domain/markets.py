"""The bets Longshot supports, and how each player stat is read from a box score.

Market keys are The Odds API's. Stat keys are '<category>.<stat>' as produced by
longshot.clients.espn.parse_boxscore.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

Stats = Mapping[str, float]

# Team bets, in the order they are offered.
TEAM_BETS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Game total"}

# The per-game markets that carry a team bet's alternate lines.
TEAM_ALTERNATES = {"spreads": "alternate_spreads", "totals": "alternate_totals"}


def _sum(*keys: str) -> Callable[[Stats], float]:
    return lambda stats: sum(stats.get(key, 0.0) for key in keys)


def _touchdowns(stats: Stats) -> float:
    # ESPN lists an interception return both as a defensive TD and as an
    # interception TD, so take the larger of the two and not their sum.
    defensive = max(
        stats.get("defensive.defensiveTouchdowns", 0.0),
        stats.get("interceptions.interceptionTouchdowns", 0.0),
    )
    return (
        stats.get("rushing.rushingTouchdowns", 0.0)
        + stats.get("receiving.receivingTouchdowns", 0.0)
        + stats.get("kickReturns.kickReturnTouchdowns", 0.0)
        + stats.get("puntReturns.puntReturnTouchdowns", 0.0)
        + defensive
    )


@dataclass(frozen=True)
class PropMarket:
    key: str
    label: str  # shown in the bet list: "Rushing yards"
    noun: str  # used in a pick's description: "rushing yards"
    positions: frozenset[str]  # ESPN position codes this bet is offered to first
    stat: Callable[[Stats], float]
    # The stat can only rise during a game, so a leg can be settled the moment it
    # passes the line. Yardage can fall again and is only settled at the final whistle.
    monotonic: bool = False
    # A yes-only bet with no line (anytime TD) rather than over/under.
    yes_only: bool = False

    @property
    def alternate_key(self) -> str | None:
        return None if self.yes_only else f"{self.key}_alternate"


def _prop(key, label, positions, stat, **kwargs) -> PropMarket:
    noun = label[0].lower() + label[1:]
    return PropMarket(key, label, noun, frozenset(positions.split()), stat, **kwargs)


PROP_MARKETS: dict[str, PropMarket] = {
    market.key: market
    for market in (
        _prop("player_pass_yds", "Passing yards", "QB", _sum("passing.passingYards")),
        _prop("player_pass_tds", "Passing TDs", "QB", _sum("passing.passingTouchdowns"), monotonic=True),
        _prop("player_pass_completions", "Pass completions", "QB", _sum("passing.completions"), monotonic=True),
        _prop("player_pass_attempts", "Pass attempts", "QB", _sum("passing.passingAttempts"), monotonic=True),
        _prop("player_pass_interceptions", "Interceptions thrown", "QB", _sum("passing.interceptions"), monotonic=True),
        _prop("player_rush_yds", "Rushing yards", "QB RB FB WR", _sum("rushing.rushingYards")),
        _prop("player_rush_attempts", "Rushing attempts", "QB RB FB", _sum("rushing.rushingAttempts"), monotonic=True),
        _prop("player_receptions", "Receptions", "RB FB WR TE", _sum("receiving.receptions"), monotonic=True),
        _prop("player_reception_yds", "Receiving yards", "RB FB WR TE", _sum("receiving.receivingYards")),
        _prop(
            "player_rush_reception_yds", "Rush + receiving yards", "RB FB WR TE",
            _sum("rushing.rushingYards", "receiving.receivingYards"),
        ),
        _prop(
            "player_pass_rush_yds", "Pass + rush yards", "QB",
            _sum("passing.passingYards", "rushing.rushingYards"),
        ),
        _prop("player_kicking_points", "Kicking points", "PK", _sum("kicking.totalKickingPoints"), monotonic=True),
        _prop("player_field_goals", "Field goals made", "PK", _sum("kicking.fieldGoalsMade"), monotonic=True),
        PropMarket(
            "player_anytime_td", "Anytime TD", "anytime TD", frozenset("QB RB FB WR TE".split()),
            _touchdowns, monotonic=True, yes_only=True,
        ),
    )
}

# Positions that appear in the /pick player list.
PICKABLE_POSITIONS = frozenset("QB RB FB WR TE PK".split())


def props_for_position(position: str) -> list[PropMarket]:
    """Every prop, with the ones that fit the position first."""
    fits = [market for market in PROP_MARKETS.values() if position in market.positions]
    rest = [market for market in PROP_MARKETS.values() if position not in market.positions]
    return fits + rest
