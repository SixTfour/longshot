"""Who a pick is about, and turning what a member typed into that.

Shared by /pick, /pickfor and /backfill.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .clients.espn import Athlete, Game, Team
from .domain.lines import Line, pick_label
from .domain.markets import PROP_MARKETS, TEAM_BETS, props_for_position
from .domain.names import normalize
from .domain.schedule import kickoff_text


@dataclass(frozen=True)
class Subject:
    """A team, or a player on a team, in one game."""

    game: Game
    team: Team
    athlete: Athlete | None = None

    @property
    def name(self) -> str:
        return self.athlete.name if self.athlete else self.team.name

    @property
    def value(self) -> str:
        return f"p:{self.athlete.id}" if self.athlete else f"t:{self.team.abbr}"

    @property
    def label(self) -> str:
        when = f"{self.game.label} {kickoff_text(self.game.kickoff)}"
        if self.athlete:
            position = f" · {self.athlete.position}" if self.athlete.position else ""
            return f"{self.athlete.name}{position} · {when}"
        return f"{self.team.name} · {when}"


def subjects_for(games: Iterable[Game], athletes: Iterable[Athlete]) -> list[Subject]:
    """Every team in these games, then every listed player on one of those teams."""
    teams = [Subject(game, team) for game in games for team in (game.away, game.home)]
    by_abbr = {subject.team.abbr: subject for subject in teams}
    players = [
        Subject(by_abbr[athlete.team].game, by_abbr[athlete.team].team, athlete)
        for athlete in athletes
        if athlete.team in by_abbr
    ]
    return teams + players


def search(subjects: list[Subject], typed: str) -> list[Subject]:
    """Teams and players matching what has been typed, best match first. Teams only if nothing is typed."""
    needle = normalize(typed)
    if not needle:
        return [subject for subject in subjects if subject.athlete is None]

    ranked = []
    for subject in subjects:
        name = normalize(subject.name)
        if name.startswith(needle) or (subject.athlete is None and subject.team.abbr.lower() == needle):
            score = 0
        elif any(word.startswith(needle) for word in name.split()):
            score = 1
        elif needle in name:
            score = 2
        else:
            continue
        ranked.append((score, subject.athlete is not None, subject.name, subject))
    return [entry[3] for entry in sorted(ranked, key=lambda entry: entry[:3])]


def resolve(subjects: list[Subject], value: str) -> Subject | None:
    """Turn the 'who' field back into a subject: a value from the list, or an exact name typed out."""
    for subject in subjects:
        if subject.value == value:
            return subject
    wanted = normalize(value)
    exact = [subject for subject in subjects if normalize(subject.name) == wanted]
    return exact[0] if len(exact) == 1 else None


def bets(subject: Subject) -> list[tuple[str, str]]:
    """The bets offered for a subject, as (key, label)."""
    if subject.athlete is None:
        return list(TEAM_BETS.items())
    return [(market.key, market.label) for market in props_for_position(subject.athlete.position)]


def resolve_bet(subject: Subject, value: str) -> str | None:
    for key, label in bets(subject):
        if value == key or value.strip().lower() == label.lower():
            return key
    return None


def bet_label(key: str) -> str:
    return TEAM_BETS.get(key) or PROP_MARKETS[key].label


def pick_row(
    parlay_key: tuple[int, int, str], discord_id: int, subject: Subject, line: Line, entered_by: int
) -> dict[str, Any]:
    """The database row for a pick on this subject and line."""
    season, week, card = parlay_key
    game, athlete = subject.game, subject.athlete
    prop = PROP_MARKETS.get(line.market)
    return {
        "season": season,
        "week": week,
        "card": card,
        "discord_id": discord_id,
        "espn_event_id": game.espn_id,
        "game": game.label,
        "kickoff": game.kickoff.isoformat(timespec="seconds"),
        "home_abbr": game.home.abbr,
        "away_abbr": game.away.abbr,
        "market": line.market,
        "side": line.side,
        "line": line.point,
        "price": line.price,
        "alt": int(line.alt),
        "team_abbr": None if athlete else subject.team.abbr,
        "player_id": athlete.id if athlete else None,
        "player_name": athlete.name if athlete else None,
        "label": pick_label(
            line, game=game.label, player=athlete.name if athlete else None, noun=prop.noun if prop else None
        ),
        "entered_by": entered_by,
    }
