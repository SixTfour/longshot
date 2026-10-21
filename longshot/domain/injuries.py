"""Early Exit, as DraftKings runs its full-game version (Early Exit+): a player
prop is void if the player leaves injured at any point, overtime included, and
does not return.

ESPN's play-by-play carries the NFL gamebook's injury notes, which name a player
the gamebook way, by team code and initial:

    DAL-S.Revel was injured during the play.
    ** Injury Update: DAL-S.Revel has returned to the game.

A player whose last note is "was injured" left and did not come back. Someone
who came back gets no protection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Where the gamebook's team code differs from ESPN's abbreviation.
GAMEBOOK_TEAMS = {"ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "LA": "LAR", "WAS": "WSH"}

EARLY_EXIT = "early_exit"  # what ESPN shows: left injured and never came back

# The commissioner's ruling with /injury, which outranks ESPN. None lets ESPN decide.
INJURED = "injured"
NOT_INJURED = "not_injured"

_NOTE = re.compile(
    r"\b([A-Z]{2,3})-([A-Z][A-Za-z']*\.\s?[A-Za-z][A-Za-z.'\- ]*?) "
    r"(was injured during the play|has returned to the game)"
)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


@dataclass(frozen=True)
class InjuryNote:
    team: str  # the gamebook's team code, as written
    name: str  # as written: 'S.Revel', 'Av.Terrell', 'A.St. Brown'
    returned: bool
    quarter: int | None = None  # 5 and up is overtime


def parse_notes(plays: Iterable[tuple[int | None, str]]) -> list[InjuryNote]:
    """Every injury note in some (quarter, play text) pairs, in order."""
    return [
        InjuryNote(match.group(1), match.group(2), match.group(3).startswith("has returned"), quarter)
        for quarter, text in plays
        for match in _NOTE.finditer(text)
    ]


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _is_named(note_name: str, full_name: str) -> bool:
    """Whether 'T.Hockenson' names 'T.J. Hockenson': the initials start the first name, the rest is the last name."""
    initials, _, last = note_name.partition(".")
    parts = full_name.split()
    while len(parts) > 2 and _letters(parts[-1]) in _SUFFIXES:
        parts.pop()
    if len(parts) < 2:
        return False
    first, surname = _letters(parts[0]), _letters(" ".join(parts[1:]))
    return bool(surname) and _letters(last) == surname and first.startswith(_letters(initials))


def injury_status(notes: Iterable[InjuryNote], full_name: str, teams: Iterable[str]) -> str | None:
    """EARLY_EXIT if ESPN shows the player left injured and never came back, else None.

    teams are the ESPN abbreviations the player could be on: their own team if
    known, otherwise both teams in the game.
    """
    allowed = set(teams)
    last = None
    for note in notes:
        if GAMEBOOK_TEAMS.get(note.team, note.team) in allowed and _is_named(note.name, full_name):
            last = note
    return EARLY_EXIT if last is not None and not last.returned else None
