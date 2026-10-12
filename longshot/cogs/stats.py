"""/stats and /leaderboard."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, seasons
from ..bot import Longshot
from ..domain.grading import WON
from ..domain.stats import MemberStats, compute, ranked


def _rate(entry: MemberStats) -> str:
    return "–" if entry.hit_rate is None else f"{entry.hit_rate:.0%}"


class Stats(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _season(
        self, season: int | None = None
    ) -> tuple[int, dict[int, MemberStats], dict[int, str], list[dict]]:
        """One season's stats per member, team names, and that season's parlays."""
        season = await seasons.resolve(self.bot, season)
        members = await self.bot.db.members()
        parlays = await self.bot.db.parlays(season)
        stats = compute(members, await self.bot.db.all_picks(season), parlays, await self.bot.db.no_shows(season))
        names = {member["discord_id"]: member["team_name"] for member in members}
        return season, stats, names, parlays

    @app_commands.command(name="stats", description="A member's parlay record for the season")
    @app_commands.describe(member="Leave empty for your own", season=seasons.HINT)
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        season: seasons.Season | None = None,
    ) -> None:
        target = member or interaction.user
        season, stats, names, parlays = await self._season(season)
        entry = stats.get(target.id)
        if entry is None:
            await interaction.response.send_message(
                f"{target.mention} is not linked to a team and has no picks on record for {season}.",
                ephemeral=True,
                allowed_mentions=render.QUIET,
            )
            return
        embed = discord.Embed(title=f"{names.get(target.id, target.display_name)} · {season}")
        embed.add_field(name="Legs hit", value=f"{entry.won} of {entry.won + entry.lost} ({_rate(entry)})")
        embed.add_field(name="Streak", value=f"{entry.streak} now · best {entry.best_streak}")
        embed.add_field(name="Parlays killed", value=str(entry.kills))
        embed.add_field(name="Lowest scorer", value=f"{entry.low_scores} time{'s' if entry.low_scores != 1 else ''}")
        embed.add_field(name="Deadlines missed", value=str(entry.no_shows))
        embed.add_field(name="Pushed or void", value=str(entry.dropped))
        if not parlays:
            embed.set_footer(text=f"No parlays are on record for {season}.")
        elif entry.unsettled:
            embed.set_footer(text=f"{entry.unsettled} leg{'s' if entry.unsettled != 1 else ''} still to be settled")
        await interaction.response.send_message(embed=embed, allowed_mentions=render.QUIET)

    @app_commands.command(name="leaderboard", description="Season standings: hit rate, streaks and parlay killers")
    @app_commands.describe(season=seasons.HINT)
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction, season: seasons.Season | None = None) -> None:
        season, stats, names, parlays = await self._season(season)
        if not stats:
            await interaction.response.send_message("No members are linked yet.", ephemeral=True)
            return
        if not parlays:
            await interaction.response.send_message(f"No parlays are on record for {season}.", ephemeral=True)
            return
        played = [parlay for parlay in parlays if parlay["result"]]
        hit = sum(1 for parlay in played if parlay["result"] == WON)
        embed = discord.Embed(
            title=f"{season} leaderboard", description="\n".join(render.leaderboard_lines(ranked(stats), names))
        )
        embed.set_footer(
            text=f"Parlays hit: {hit} of {len(played)}\n"
            "Ranked by legs hit. 💀 = their leg was the first to miss. 📉 = weeks as lowest scorer. "
            "⏰ = deadlines missed."
        )
        await interaction.response.send_message(embed=embed)

    @stats.autocomplete("season")
    @leaderboard.autocomplete("season")
    async def _season_choices(self, interaction: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
        return await seasons.choices(self.bot, current)


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Stats(bot))
