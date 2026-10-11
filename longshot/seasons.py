"""The optional `season` on commands that look back at earlier years."""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands

from .clients.espn import EspnError

if TYPE_CHECKING:
    from .bot import Longshot

# A season is named by the year it starts in: the 2026 season runs into January 2027.
Season = app_commands.Range[int, 2000, 2100]
HINT = "An earlier season, by the year it started. Leave empty for this season."


async def resolve(bot: Longshot, season: int | None) -> int:
    """The season asked for, or the current one."""
    return season if season is not None else await bot.nfl.season()


async def choices(bot: Longshot, current: object) -> list[app_commands.Choice[int]]:
    """Seasons with parlays on record, newest first, plus the current one."""
    seasons = set(await bot.db.seasons())
    try:
        seasons.add(await bot.nfl.season())
    except EspnError:
        pass
    typed = str(current or "").strip()
    return [
        app_commands.Choice(name=str(season), value=season)
        for season in sorted(seasons, reverse=True)
        if str(season).startswith(typed)
    ][:25]
