"""/paid and /ledger: who owes the stake, what has been collected, and what has been won."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, seasons
from ..bot import Longshot, commissioner_only
from ..domain.grading import WON
from ..domain.odds_math import format_money


class Ledger(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot

    @app_commands.command(name="paid", description="Mark a member's stake as collected")
    @app_commands.describe(member="Who paid", week="The parlay week they owed for. Leave empty for their oldest debt.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def paid(
        self, interaction: discord.Interaction, member: discord.Member, week: app_commands.Range[int, 1, 18] | None = None
    ) -> None:
        linked = await self.bot.db.member(member.id)
        if linked is None:
            await interaction.response.send_message(
                f"{member.mention} is not linked to a team.", ephemeral=True, allowed_mentions=render.QUIET
            )
            return
        owed = [
            debt
            for debt in await self.bot.db.debts()
            if debt["roster_id"] == linked["roster_id"] and not debt["paid_at"] and week in (None, debt["week"])
        ]
        if not owed:
            await interaction.response.send_message(
                f"{member.mention} has nothing outstanding{'' if week is None else f' for Week {week}'}.",
                ephemeral=True,
                allowed_mentions=render.QUIET,
            )
            return
        debt = owed[0]
        await self.bot.db.mark_paid(debt["season"], debt["week"])
        left = len(owed) - 1
        more = f" They still owe for {left} more week{'s' if left != 1 else ''}." if left else ""
        await interaction.response.send_message(
            f"✅ {member.mention} paid {format_money(debt['amount'])} for the Week {debt['week']} parlay.{more}",
            allowed_mentions=render.QUIET,
        )

    @app_commands.command(name="ledger", description="Debts, money collected and winnings for the season")
    @app_commands.describe(season=seasons.HINT)
    @app_commands.guild_only()
    async def ledger(self, interaction: discord.Interaction, season: seasons.Season | None = None) -> None:
        season = await seasons.resolve(self.bot, season)
        debts = await self.bot.db.debts(season)
        weeks = await self.bot.db.parlays(season)

        def who(debt: dict) -> str:
            return f"**{debt['team_name']}**"  # the team's name when the debt was recorded

        unpaid = [debt for debt in debts if not debt["paid_at"]]
        collected = sum(debt["amount"] for debt in debts if debt["paid_at"])
        staked = sum(week["stake"] or 0 for week in weeks if week["placed_at"])
        won = sum(week["payout"] or 0 for week in weeks if week["result"] == WON)

        embed = discord.Embed(title=f"{season} parlay ledger")
        embed.add_field(
            name="Outstanding",
            value="\n".join(f"{who(debt)} · {format_money(debt['amount'])} · Week {debt['week']}" for debt in unpaid)
            or "Nobody owes anything.",
            inline=False,
        )
        embed.add_field(name="Collected", value=format_money(collected))
        embed.add_field(name="Staked on placed parlays", value=format_money(staked))
        embed.add_field(name="Won", value=format_money(won))
        history = [
            f"Week {debt['week']} parlay · {who(debt)} · {'paid' if debt['paid_at'] else '**unpaid**'}"
            for debt in debts[-10:]
        ]
        if history:
            embed.add_field(name="Who owed the stake", value="\n".join(history), inline=False)
        await interaction.response.send_message(embed=embed, allowed_mentions=render.QUIET)

    @ledger.autocomplete("season")
    async def _season_choices(self, interaction: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
        return await seasons.choices(self.bot, current)


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Ledger(bot))
