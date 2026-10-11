"""/admin: run a step by hand, settle who pays, and check the bot's state."""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, tracking, weekly
from ..bot import Longshot, commissioner_only
from ..db import utcnow
from ..domain.odds_math import format_money
from ..domain.schedule import stamp
from ..views import paid_view

STEPS = [
    app_commands.Choice(name="announce: post the lowest scorer and open picks", value="announce"),
    app_commands.Choice(name="remind: nudge members who have not picked", value="remind"),
    app_commands.Choice(name="close: close picks and post the slip", value="close"),
    app_commands.Choice(name="track: check live games and grade legs", value="track"),
    app_commands.Choice(name="recap: post the week's result", value="recap"),
]


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class Admin(commands.GroupCog, group_name="admin", group_description="Commissioner tools"):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _week(self, week: int | None) -> tuple[int, int] | None:
        if week is not None:
            return await self.bot.nfl.season(), week
        context = await self.bot.nfl.active(utcnow())
        return (context.season, context.week) if context else None

    @app_commands.command(name="run", description="Run one step of the weekly cycle now, whatever the clock says")
    @app_commands.describe(step="Which step", week="The parlay week. Leave empty for this week.")
    @app_commands.choices(step=STEPS)
    @commissioner_only()
    async def run(
        self,
        interaction: discord.Interaction,
        step: app_commands.Choice[str],
        week: app_commands.Range[int, 1, 18] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = await self._week(week)
        context = await self.bot.nfl.context(*target) if target else None
        if context is None:
            await interaction.followup.send("There is no such week in the regular season right now.")
            return
        now = utcnow()
        if step.value == "announce":
            done = await weekly.announce(self.bot, context, now, force=True)
        elif step.value == "remind":
            done = await weekly.remind(self.bot, context, now, force=True)
        elif step.value == "close":
            done = await weekly.close_picks(self.bot, context, now, force=True)
        elif step.value == "track":
            await tracking.track_week(self.bot, context.season, context.week, now)
            done = True
        else:
            done = await weekly.recap(self.bot, context.season, context.week, force=True)
        outcome = "Done." if done else "Nothing to do (or the league channel is not set up; see `/admin status`)."
        await interaction.followup.send(f"`{step.value}` for Week {context.week}: {outcome}")

    @app_commands.command(name="loser", description="Set who owes the stake for a week's parlay (ties, or to correct it)")
    @app_commands.describe(member="Who pays", week="The parlay week they are funding. Leave empty for this week.")
    @commissioner_only()
    async def loser(
        self, interaction: discord.Interaction, member: discord.Member, week: app_commands.Range[int, 1, 18] | None = None
    ) -> None:
        linked = await self.bot.db.member(member.id)
        target = await self._week(week)
        if linked is None or target is None:
            await interaction.response.send_message(
                f"{member.mention} is not linked to a team." if linked is None else "There is no parlay running.",
                ephemeral=True,
                allowed_mentions=render.QUIET,
            )
            return
        season, week_number = target
        row = await self.bot.db.ensure_week(season, week_number)
        # Keep the score if this settles a tie; a different pick has no score to show.
        tied = row["tied_roster_ids"] and linked["roster_id"] in json.loads(row["tied_roster_ids"])
        await weekly.record_loser(self.bot, season, week_number, linked, row["loser_points"] if tied else None)
        stake = await self.bot.settings.stake()
        await interaction.response.send_message(
            f"{member.mention} (**{linked['team_name']}**) owes **{format_money(stake)}** for the "
            f"Week {week_number} parlay.",
            view=paid_view(season, week_number),
            allowed_mentions=render.PING_USERS,
        )

    @app_commands.command(name="status", description="Check how the bot is set up and what it will do next")
    @commissioner_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = self.bot
        now = utcnow()
        channel = await bot.league_channel()
        league = await bot.db.get_state("league_name")
        lines = [
            f"**League:** {league or 'not connected (run `/setup`)'}",
            f"**Channel:** {channel.mention if channel else 'not set or not reachable'}",
            f"**Members linked:** {len(await bot.db.members())}",
            f"**Sportsbook:** {await bot.settings.sportsbook_name()}",
        ]
        context = await bot.nfl.active(now)
        if context is None:
            lines.append("**Week:** none running (outside the regular season, or before the first announcement)")
        else:
            row = await bot.db.week(context.season, context.week) or {}
            picks = await bot.db.week_picks(context.season, context.week)
            moved = " (moved up for an early first game)" if context.times.moved_early else ""
            state = (
                "result posted" if row.get("recap_at")
                else "picks closed" if row.get("closed_at")
                else "picks open" if row.get("announced_at")
                else "waiting to announce"
            )
            lines += [
                f"**Week {context.week}:** {state} · {len(picks)} picks in",
                f"**Announcement:** {stamp(context.times.announce_at, 'F')}",
                f"**Deadline:** {stamp(context.times.deadline_at, 'F')}{moved}",
            ]
        remaining, used = await bot.linebook.usage()
        lines.append(
            "**Odds API credits:** no call made yet"
            if remaining is None
            else f"**Odds API credits:** {remaining} left, {used if used is not None else '?'} used this month"
        )
        await interaction.followup.send("\n".join(lines))


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Admin(bot))
