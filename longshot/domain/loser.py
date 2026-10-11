"""Finding the week's lowest fantasy score from Sleeper matchups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def roster_scores(matchups: Iterable[Mapping[str, Any]]) -> dict[int, float]:
    """Points per roster. A commissioner's manual override (custom_points) wins."""
    scores: dict[int, float] = {}
    for entry in matchups:
        roster_id = entry.get("roster_id")
        points = entry.get("custom_points")
        if points is None:
            points = entry.get("points")
        if roster_id is None or points is None:
            continue
        scores[int(roster_id)] = float(points)
    return scores


def lowest_scorers(matchups: Iterable[Mapping[str, Any]]) -> tuple[float, list[int]] | None:
    """The lowest score and every roster that posted it, or None if the week has no scores yet."""
    scores = roster_scores(matchups)
    if not scores or not any(scores.values()):
        return None
    low = min(scores.values())
    return low, sorted(roster for roster, points in scores.items() if points == low)
