"""/slip, /placed and /grade: the parlays and their results."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, seasons, tracking
from ..bot import Longshot, commissioner_only
from ..db import iso, utcnow
from ..domain.cards import BONUS, MAIN, card_title
from ..domain.grading import LOST, PENDING, PUSH, VOID, WON
from ..domain.odds_math import american_to_decimal, format_american, format_money, parse_american, total_return
from ..domain.schedule import stamp

RESULTS = [
    app_commands.Choice(name="Won", value=WON),
    app_commands.Choice(name="Lost", value=LOST),
    app_commands.Choice(name="Push (drops out of the parlay)", value=PUSH),
    app_commands.Choice(name="Void (drops out of the parlay)", value=VOID),
    app_commands.Choice(name="Undo: let the bot grade it again", value=PENDING),
]

# Only Thanksgiving week has more than one parlay, so this option is rarely needed.
PARLAYS = [
    app_commands.Choice(name="The weekly parlay", value=MAIN),
    app_commands.Choice(name="Thanksgiving bonus", value=BONUS),
]
WHICH = "That week has two parlays. Say which one with the `parlay` option."


class Slip(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _parlays(
        self, week: int | None, card: str | None = None, season: int | None = None
    ) -> list[dict[str, Any]]:
        """The announced parlays of one week, optionally just one of them.

        With no week, that is the live week. For an earlier season it is the
        last week that season has on record.
        """
        plan = await self.bot.nfl.active(utcnow())
        current = plan.season if plan else await self.bot.nfl.season()
        season = current if season is None else season
        if week is None:
            if season == current:
                if plan is None:
                    return []
                week = plan.week
            else:
                on_record = await self.bot.db.parlays(season)
                if not on_record:
                    return []
                week = on_record[-1]["week"]
        rows = await self.bot.db.parlays(season, week)
        return [row for row in rows if row["announced_at"] and card in (None, row["card"])]

    @app_commands.command(name="slip", description="Show the parlay and how each leg is doing")
    @app_commands.describe(week="A past week's slip. Leave empty for this week.", season=seasons.HINT)
    @app_commands.guild_only()
    async def slip(
        self,
        interaction: discord.Interaction,
        week: app_commands.Range[int, 1, 18] | None = None,
        season: seasons.Season | None = None,
    ) -> None:
        rows = await self._parlays(week, season=season)
        if not rows:
            await interaction.response.send_message("There is no parlay on record for that week.", ephemeral=True)
            return
        members = [member["discord_id"] for member in await self.bot.db.members()]
        this_season = await self.bot.nfl.season()
        embeds = []
        for row in rows:
            key = (row["season"], row["week"], row["card"])
            picks = await self.bot.db.parlay_picks(*key)
            # A slip from an earlier year says which year.
            title = f"{row['season']} · {card_title(row['week'], row['card'])} parlay"
            embed = render.slip_embed(
                row, picks, row["stake"] or await self.bot.settings.stake(),
                title=title if row["season"] != this_season else None,
            )
            if not row["closed_at"]:
                picked = {pick["discord_id"] for pick in picks}
                waiting = [discord_id for discord_id in members if discord_id not in picked]
                if waiting:
                    embed.add_field(name="Waiting on", value=render.mentions(waiting), inline=False)
                parlay = await self.bot.nfl.parlay(*key)
                if parlay:
                    embed.add_field(name="Picks close", value=stamp(parlay.times.deadline_at, "R"), inline=False)
            embeds.append(embed)
        await interaction.response.send_message(embeds=embeds, allowed_mentions=render.QUIET)

    @slip.autocomplete("season")
    async def _season_choices(self, interaction: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
        return await seasons.choices(self.bot, current)

    @app_commands.command(name="placed", description="Record that the parlay was placed, with the sportsbook's real odds")
    @app_commands.describe(
        odds="The parlay's American odds from your bet slip, such as +125000",
        parlay="Only needed in Thanksgiving week, when there are two",
    )
    @app_commands.choices(parlay=PARLAYS)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def placed(
        self, interaction: discord.Interaction, odds: str, parlay: app_commands.Choice[str] | None = None
    ) -> None:
        try:
            price = parse_american(odds)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        rows = [
            row
            for row in await self._parlays(None, parlay.value if parlay else None)
            if await self.bot.db.parlay_picks(row["season"], row["week"], row["card"])
        ]
        if len(rows) > 1:
            rows = [row for row in rows if not row["placed_at"]] or rows
        if not rows:
            await interaction.response.send_message("There are no picks to place a parlay on yet.", ephemeral=True)
            return
        if len(rows) > 1:
            await interaction.response.send_message(WHICH, ephemeral=True)
            return
        row = rows[0]
        await self.bot.db.update_parlay(
            row["season"], row["week"], row["card"], placed_at=iso(utcnow()), placed_odds=price
        )
        stake = row["stake"] or await self.bot.settings.stake()
        pays = total_return(stake, american_to_decimal(price))
        await interaction.response.send_message(
            f"🎟️ **The {card_title(row['week'], row['card'])} parlay is placed** at {format_american(price)}. "
            f"{format_money(stake)} returns **{format_money(pays)}** if every leg hits."
        )

    @app_commands.command(name="grade", description="Settle a leg by hand, or correct one the bot graded")
    @app_commands.describe(
        member="Whose leg it is",
        result="How it finished",
        week="The week. Leave empty for this week.",
        parlay="Only needed in Thanksgiving week, when there are two",
    )
    @app_commands.choices(result=RESULTS, parlay=PARLAYS)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def grade(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        result: app_commands.Choice[str],
        week: app_commands.Range[int, 1, 18] | None = None,
        parlay: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        picks = []
        for row in await self._parlays(week, parlay.value if parlay else None):
            pick = await self.bot.db.member_pick(row["season"], row["week"], row["card"], member.id)
            if pick is not None:
                picks.append((row, pick))
        if not picks:
            await interaction.followup.send(
                f"{member.mention} has no pick for that week.", allowed_mentions=render.QUIET
            )
            return
        if len(picks) > 1:
            await interaction.followup.send(WHICH)
            return

        row, pick = picks[0]
        key = (row["season"], row["week"], row["card"])
        status = result.value
        note = None if status == PENDING else "Settled by the commissioner"
        await self.bot.db.grade_pick(
            pick["id"], status, None if status == PENDING else pick["stat_value"], note, str(interaction.user.id)
        )
        # A parlay already summed up is reopened, so the corrected result is posted once every leg is settled.
        if row["recap_at"]:
            await self.bot.db.update_parlay(*key, recap_at=None, result=None, payout=None)
        await tracking.announce_grades(self.bot, *key, [] if status == PENDING else [pick["id"]])
        text = (
            "will be graded by the bot again on its next check"
            if status == PENDING
            else f"is settled as **{result.name.split(' (')[0].lower()}**"
        )
        await interaction.followup.send(
            f"{member.mention}'s {card_title(row['week'], row['card'])} leg ({render.leg(pick)}) {text}.",
            allowed_mentions=render.QUIET,
        )


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Slip(bot))
