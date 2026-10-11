"""Matching player and team names across data sources that spell them differently."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Abbreviations other sources use, mapped to ESPN's.
_TEAM_ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def team_key(abbr: str) -> str:
    upper = abbr.strip().upper()
    return _TEAM_ALIASES.get(upper, upper)


def normalize(name: str) -> str:
    """'D.J. Moore Jr.' -> 'dj moore'. Accents, punctuation and suffixes are dropped."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[.'’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    parts = [part for part in text.split() if part not in _SUFFIXES]
    return " ".join(parts)


def _initial_and_last(name: str) -> tuple[str, str] | None:
    parts = normalize(name).split()
    if len(parts) < 2:
        return None
    return parts[0][0], parts[-1]


def match_name(target: str, candidates: Iterable[str]) -> str | None:
    """Return the candidate that is the same person as target, or None.

    An exact match after normalising wins. Otherwise a single candidate sharing
    the first initial and last name is accepted ('Gabe Davis' / 'Gabriel Davis').
    Anything ambiguous returns None so that a wrong player is never chosen.
    """
    pool = list(candidates)
    wanted = normalize(target)
    for candidate in pool:
        if normalize(candidate) == wanted:
            return candidate

    key = _initial_and_last(target)
    if key is None:
        return None
    loose = {candidate for candidate in pool if _initial_and_last(candidate) == key}
    return loose.pop() if len(loose) == 1 else None
