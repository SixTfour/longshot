"""/slip, /placed and /grade: the week's parlay and its results."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, tracking
from ..bot import Longshot, commissioner_only
from ..db import iso, utcnow
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


class Slip(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _target_week(self, week: int | None) -> tuple[int, int] | None:
        """The requested week of this season, or the live one."""
        context = await self.bot.nfl.active(utcnow())
        if week is not None:
            season = context.season if context else await self.bot.nfl.season()
            return season, week
        return (context.season, context.week) if context else None

    @app_commands.command(name="slip", description="Show the parlay and how each leg is doing")
    @app_commands.describe(week="A past week's slip. Leave empty for this week.")
    @app_commands.guild_only()
    async def slip(self, interaction: discord.Interaction, week: app_commands.Range[int, 1, 18] | None = None) -> None:
        target = await self._target_week(week)
        row = await self.bot.db.week(*target) if target else None
        if row is None:
            await interaction.response.send_message("There is no parlay for that week.", ephemeral=True)
            return
        picks = await self.bot.db.week_picks(*target)
        stake = row["stake"] or await self.bot.settings.stake()
        embed = render.slip_embed(row, picks, stake)
        if not row["closed_at"]:
            context = await self.bot.nfl.context(*target)
            picked = {pick["discord_id"] for pick in picks}
            waiting = [m["discord_id"] for m in await self.bot.db.members() if m["discord_id"] not in picked]
            if waiting:
                embed.add_field(name="Waiting on", value=render.mentions(waiting), inline=False)
            if context:
                embed.set_footer(text="Picks are still open")
                embed.add_field(name="Picks close", value=stamp(context.times.deadline_at, "R"), inline=False)
        await interaction.response.send_message(embed=embed, allowed_mentions=render.QUIET)

    @app_commands.command(name="placed", description="Record that the parlay was placed, with the sportsbook's real odds")
    @app_commands.describe(odds="The parlay's American odds from your bet slip, such as +125000")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def placed(self, interaction: discord.Interaction, odds: str) -> None:
        try:
            price = parse_american(odds)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        target = await self._target_week(None)
        row = await self.bot.db.week(*target) if target else None
        if row is None or not await self.bot.db.week_picks(*target):
            await interaction.response.send_message("There are no picks to place a parlay on yet.", ephemeral=True)
            return
        await self.bot.db.update_week(*target, placed_at=iso(utcnow()), placed_odds=price)
        stake = row["stake"] or await self.bot.settings.stake()
        pays = total_return(stake, american_to_decimal(price))
        await interaction.response.send_message(
            f"🎟️ **The Week {target[1]} parlay is placed** at {format_american(price)}. "
            f"{format_money(stake)} returns **{format_money(pays)}** if every leg hits."
        )

    @app_commands.command(name="grade", description="Settle a leg by hand, or correct one the bot graded")
    @app_commands.describe(
        member="Whose leg it is", result="How it finished", week="The week. Leave empty for this week."
    )
    @app_commands.choices(result=RESULTS)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def grade(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        result: app_commands.Choice[str],
        week: app_commands.Range[int, 1, 18] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = await self._target_week(week)
        pick = await self.bot.db.member_pick(*target, member.id) if target else None
        if pick is None:
            await interaction.followup.send(
                f"{member.mention} has no pick for that week.", allowed_mentions=render.QUIET
            )
            return
        season, week_number = target
        status = result.value
        note = None if status == PENDING else "Settled by the commissioner"
        await self.bot.db.grade_pick(
            pick["id"], status, None if status == PENDING else pick["stat_value"], note, str(interaction.user.id)
        )
        # A week already summed up is reopened, so the corrected result is posted once every leg is settled.
        row = await self.bot.db.week(season, week_number)
        if row and row["recap_at"]:
            await self.bot.db.update_week(season, week_number, recap_at=None, result=None, payout=None)
        await tracking.announce_grades(self.bot, season, week_number, [] if status == PENDING else [pick["id"]])
        text = (
            "will be graded by the bot again on its next check"
            if status == PENDING
            else f"is settled as **{result.name.split(' (')[0].lower()}**"
        )
        await interaction.followup.send(
            f"{member.mention}'s Week {week_number} leg ({render.leg(pick)}) {text}.", allowed_mentions=render.QUIET
        )


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Slip(bot))
