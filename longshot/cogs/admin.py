"""/admin: run a step by hand, settle who pays, and check the bot's state."""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, tracking, weekly
from ..bot import Longshot, commissioner_only
from ..db import utcnow
from ..domain.cards import BONUS, MAIN
from ..domain.odds_math import format_money
from ..domain.schedule import stamp
from ..views import paid_view

STEPS = [
    app_commands.Choice(name="announce: post the lowest scorer and open picks", value="announce"),
    app_commands.Choice(name="remind: nudge members who have not picked", value="remind"),
    app_commands.Choice(name="close: close picks and post the slip", value="close"),
    app_commands.Choice(name="track: check live games and grade legs", value="track"),
    app_commands.Choice(name="recap: post the result", value="recap"),
]
PARLAYS = [
    app_commands.Choice(name="The weekly parlay", value=MAIN),
    app_commands.Choice(name="Thanksgiving bonus", value=BONUS),
]


@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
class Admin(commands.GroupCog, group_name="admin", group_description="Commissioner tools"):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _week(self, week: int | None) -> tuple[int, int] | None:
        if week is not None:
            return await self.bot.nfl.season(), week
        plan = await self.bot.nfl.active(utcnow())
        return (plan.season, plan.week) if plan else None

    @app_commands.command(name="run", description="Run one step of the weekly cycle now, whatever the clock says")
    @app_commands.describe(
        step="Which step",
        week="The parlay week. Leave empty for this week.",
        parlay="Thanksgiving week only: run the step for one of its two parlays",
    )
    @app_commands.choices(step=STEPS, parlay=PARLAYS)
    @commissioner_only()
    async def run(
        self,
        interaction: discord.Interaction,
        step: app_commands.Choice[str],
        week: app_commands.Range[int, 1, 18] | None = None,
        parlay: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = await self._week(week)
        plan = await self.bot.nfl.plan(*target) if target else None
        if plan is None:
            await interaction.followup.send("There is no such week in the regular season right now.")
            return
        now = utcnow()
        chosen = [item for item in plan.parlays if parlay is None or item.card == parlay.value]
        if step.value == "announce":
            done = await weekly.announce(self.bot, plan, now, force=True)
        elif step.value == "track":
            await tracking.track_week(self.bot, plan.season, plan.week)
            done = True
        else:
            results = []
            for item in chosen:
                if step.value == "remind":
                    results.append(await weekly.remind(self.bot, item, now, force=True))
                elif step.value == "close":
                    results.append(await weekly.close_picks(self.bot, item, now, force=True))
                else:
                    results.append(await weekly.recap(self.bot, *item.key, force=True))
            done = any(results)
        outcome = "Done." if done else "Nothing to do (or the league channel is not set up; see `/admin status`)."
        await interaction.followup.send(f"`{step.value}` for Week {plan.week}: {outcome}")

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
        row = await self.bot.db.ensure_parlay(season, week_number, MAIN)
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
        league_year = await bot.db.get_state("league_season")
        week_now = await bot.nfl.regular_week(now)
        nfl_year = await bot.nfl.season()
        named = f"{league} ({league_year} season)" if league and league_year else league
        lines = [
            f"**League:** {named or 'not connected (run `/setup`)'}",
            f"**Channel:** {channel.mention if channel else 'not set or not reachable'}",
            f"**Members linked:** {len(await bot.db.members())}",
            f"**Sportsbook:** {await bot.settings.sportsbook_name()}",
        ]
        if league_year and week_now is not None and int(league_year) != nfl_year:
            lines.append(
                f"⚠️ That league is from {league_year}, and the NFL is in its {nfl_year} season. Run `/setup` with "
                "this season's league ID."
            )
        plan = await bot.nfl.active(now)
        if week_now is None:
            lines.append("**Season:** not in the regular season, so nothing is being posted")
        elif plan is None:
            lines.append("**Week:** waiting for the first announcement of the season")
        else:
            lines.append(f"**Week {plan.week} announcement:** {stamp(plan.announce_at, 'F')}")
            for parlay in plan.parlays:
                row = await bot.db.parlay(*parlay.key) or {}
                picks = await bot.db.parlay_picks(*parlay.key)
                state = (
                    "result posted" if row.get("recap_at")
                    else "picks closed" if row.get("closed_at")
                    else "picks open" if row.get("announced_at")
                    else "waiting to announce"
                )
                lines.append(
                    f"**{parlay.title} parlay:** {state} · {len(picks)} picks in · deadline "
                    f"{stamp(parlay.times.deadline_at, 'F')}"
                )
        remaining, used = await bot.linebook.usage()
        lines.append(
            "**Odds API credits:** no call made yet"
            if remaining is None
            else f"**Odds API credits:** {remaining} left, {used if used is not None else '?'} used this month"
        )
        await interaction.followup.send("\n".join(lines))


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Admin(bot))
