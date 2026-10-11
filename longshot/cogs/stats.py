"""/stats and /leaderboard."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import render
from ..bot import Longshot
from ..domain.grading import WON
from ..domain.stats import MemberStats, compute, ranked


def _rate(entry: MemberStats) -> str:
    return "–" if entry.hit_rate is None else f"{entry.hit_rate:.0%}"


class Stats(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _season(self) -> tuple[int, dict[int, MemberStats], dict[int, str], list[dict]]:
        season = await self.bot.nfl.season()
        members = await self.bot.db.members()
        weeks = await self.bot.db.weeks(season)
        stats = compute(members, await self.bot.db.all_picks(season), weeks, await self.bot.db.no_shows(season))
        names = {member["discord_id"]: member["team_name"] for member in members}
        return season, stats, names, weeks

    @app_commands.command(name="stats", description="A member's parlay record for the season")
    @app_commands.describe(member="Leave empty for your own")
    @app_commands.guild_only()
    async def stats(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        season, stats, names, _ = await self._season()
        entry = stats.get(target.id)
        if entry is None:
            await interaction.response.send_message(
                f"{target.mention} is not linked to a team and has no picks on record.",
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
        if entry.unsettled:
            embed.set_footer(text=f"{entry.unsettled} leg{'s' if entry.unsettled != 1 else ''} still to be settled")
        await interaction.response.send_message(embed=embed, allowed_mentions=render.QUIET)

    @app_commands.command(name="leaderboard", description="Season standings: hit rate, streaks and parlay killers")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        season, stats, names, weeks = await self._season()
        if not stats:
            await interaction.response.send_message("No members are linked yet.", ephemeral=True)
            return
        rows = [f"{'#':>2}  {'Team':<18} {'Hit':>4}  {'W-L':>5}  {'Strk':>4}  {'Kills':>5}  {'Low':>3}"]
        for position, entry in enumerate(ranked(stats), start=1):
            name = names.get(entry.discord_id, "(unlinked)")[:18]
            rows.append(
                f"{position:>2}  {name:<18} {_rate(entry):>4}  {f'{entry.won}-{entry.lost}':>5}  "
                f"{entry.streak:>4}  {entry.kills:>5}  {entry.low_scores:>3}"
            )
        played = [week for week in weeks if week["result"]]
        hit = sum(1 for week in played if week["result"] == WON)
        embed = discord.Embed(title=f"{season} leaderboard", description="```\n" + "\n".join(rows) + "\n```")
        embed.set_footer(
            text=f"Parlays hit: {hit} of {len(played)} · Strk = current run of hits · Kills = first miss on a slip · "
            "Low = weeks as lowest scorer"
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Stats(bot))
