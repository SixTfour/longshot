"""The live slip: the message posted when picks close, edited in place as the games are played.

Editing one message keeps the channel quiet. Nobody is notified by an edit, and
the slip is always the current picture of the parlay.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

import discord

from . import render
from .clients.espn import Game
from .domain.cards import card_title

if TYPE_CHECKING:
    from .bot import Longshot

log = logging.getLogger(__name__)


async def refresh(
    bot: Longshot, season: int, week: int, card: str, games: Mapping[str, Game] | None = None
) -> bool:
    """Bring the posted slip up to date. Returns True if the message was edited.

    Does nothing if no slip was posted for the parlay, or if nothing on it has changed.
    """
    row = await bot.db.parlay(season, week, card)
    if row is None or not row["slip_message_id"]:
        return False
    channel = await bot.league_channel()
    if channel is None:
        return False

    picks = await bot.db.parlay_picks(season, week, card)
    stake = row["stake"] or await bot.settings.stake()
    embed = render.slip_embed(
        row, picks, stake, await bot.team_names(), title=f"{card_title(week, card)} slip", games=games
    )
    key = (season, week, card)
    showing = (embed.description, embed.colour, [field.value for field in embed.fields])
    if bot.slips_showing.get(key) == showing:
        return False
    try:
        await channel.get_partial_message(row["slip_message_id"]).edit(embed=embed)
    except discord.NotFound:
        # The slip was deleted, or the league channel has changed since. Stop trying.
        await bot.db.update_parlay(season, week, card, slip_message_id=None)
        return False
    except discord.HTTPException as error:
        log.warning("Could not update the Week %s slip: %s", week, error)
        return False
    bot.slips_showing[key] = showing
    return True
