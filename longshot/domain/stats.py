"""Season statistics per member, computed from stored picks and weeks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .grading import DROPPED, LOST, WON

Row = Mapping[str, Any]


@dataclass
class MemberStats:
    discord_id: int
    legs: int = 0
    won: int = 0
    lost: int = 0
    dropped: int = 0  # pushes and voids
    unsettled: int = 0
    streak: int = 0  # current run of hits
    best_streak: int = 0
    kills: int = 0  # weeks where this member's leg was the first to miss
    low_scores: int = 0  # weeks as the lowest fantasy scorer
    no_shows: int = 0  # deadlines missed

    @property
    def hit_rate(self) -> float | None:
        decided = self.won + self.lost
        return self.won / decided if decided else None


def compute(
    members: Iterable[Row], picks: Iterable[Row], weeks: Iterable[Row], no_shows: Iterable[Row]
) -> dict[int, MemberStats]:
    """Stats for every linked member, plus anyone unlinked who still has picks on record."""
    members = list(members)
    stats = {member["discord_id"]: MemberStats(member["discord_id"]) for member in members}
    by_roster = {member["roster_id"]: member["discord_id"] for member in members}
    owner_of_pick: dict[int, int] = {}

    for pick in sorted(picks, key=lambda row: (row["season"], row["week"], row["id"])):
        entry = stats.setdefault(pick["discord_id"], MemberStats(pick["discord_id"]))
        owner_of_pick[pick["id"]] = pick["discord_id"]
        entry.legs += 1
        if pick["status"] == WON:
            entry.won += 1
            entry.streak += 1
            entry.best_streak = max(entry.best_streak, entry.streak)
        elif pick["status"] == LOST:
            entry.lost += 1
            entry.streak = 0
        elif pick["status"] in DROPPED:
            entry.dropped += 1  # neither extends nor breaks a streak
        else:
            entry.unsettled += 1

    for week in weeks:
        killer = owner_of_pick.get(week["killer_pick_id"])
        if killer is not None:
            stats[killer].kills += 1
        # Who held the team when it happened, if that was recorded; otherwise who holds it now.
        loser = week.get("loser_discord_id") or by_roster.get(week["loser_roster_id"])
        if loser is not None:
            stats.setdefault(loser, MemberStats(loser)).low_scores += 1

    for row in no_shows:
        if row["discord_id"] in stats:
            stats[row["discord_id"]].no_shows += 1
    return stats


def ranked(stats: Mapping[int, MemberStats]) -> list[MemberStats]:
    """Best hit rate first; members with no decided legs last."""
    return sorted(
        stats.values(),
        key=lambda entry: (entry.hit_rate is None, -(entry.hit_rate or 0), -entry.won, entry.lost, entry.discord_id),
    )
