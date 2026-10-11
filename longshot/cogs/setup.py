"""/setup, /link, /unlink and /members: connecting the league and its people."""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from .. import render
from ..bot import Longshot, commissioner_only
from ..clients.espn import EspnError
from ..domain.fantasy import league_season

TEAMS_TTL = 300  # seconds


class Setup(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot
        self._teams: tuple[float, list[dict]] | None = None

    async def _league_teams(self) -> list[dict]:
        league_id = await self.bot.league_id()
        if league_id is None:
            return []
        if self._teams is None or time.monotonic() - self._teams[0] > TEAMS_TTL:
            self._teams = (time.monotonic(), await self.bot.sleeper.teams(league_id))
        return self._teams[1]

    @app_commands.command(name="setup", description="Connect your Sleeper league and choose where Longshot posts")
    @app_commands.describe(
        league_id="The long number in your Sleeper league's web address",
        channel="The channel for announcements, picks and results",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def setup(self, interaction: discord.Interaction, league_id: str, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True)
        league_id = league_id.strip()
        if not league_id.isdigit():
            await interaction.followup.send("A Sleeper league ID is a long number, such as `1048301234567890123`.")
            return
        league = await self.bot.sleeper.league(league_id)
        if league.get("sport") != "nfl":
            await interaction.followup.send("That Sleeper league is not an NFL league.")
            return

        # Sleeper gives a renewed league a new ID each season; last season's would give last season's scores.
        league_year = league_season(league)
        try:
            nfl_year = await self.bot.nfl.season()
        except EspnError:
            nfl_year = None
        if league_year and nfl_year and league_year < nfl_year:
            await interaction.followup.send(
                f"That is your **{league_year}** league, and the NFL is in its **{nfl_year}** season. Sleeper gives "
                f"each season's league a new ID. Open the {nfl_year} league in Sleeper and use the number from its "
                "web address."
            )
            return

        mine = channel.permissions_for(interaction.guild.me)
        if not (mine.view_channel and mine.send_messages and mine.embed_links):
            await interaction.followup.send(
                f"I cannot post in {channel.mention}. Give me View Channel, Send Messages and Embed Links there, "
                "then run `/setup` again."
            )
            return

        for key, value in (
            ("league_id", league_id),
            ("league_name", league.get("name", "")),
            ("league_season", league_year or ""),
            ("channel_id", channel.id),
            ("commissioner_id", interaction.user.id),
        ):
            await self.bot.db.set_state(key, value)
        self._teams = None
        self.bot.forget_league()
        fantasy = await self.bot.fantasy()
        teams = await self._league_teams()
        linked = {member["roster_id"] for member in await self.bot.db.members()}
        await interaction.followup.send(
            f"Connected to **{league.get('name', 'your league')}** ({league_year} season, {len(teams)} teams). I will post in "
            f"{channel.mention}, and you are the commissioner.\n"
            f"{len(linked & {team['roster_id'] for team in teams})} of {len(teams)} teams are linked to a Discord "
            "member. Link each one with `/link`, then check with `/members`.\n"
            f"Sleeper has the fantasy season as {fantasy.span}; parlays run for those weeks only.\n"
            "Settings such as the deadline and sportsbook are under `/config`."
        )

    @app_commands.command(name="link", description="Tie a Discord member to their Sleeper team")
    @app_commands.describe(member="The Discord member", team="Their Sleeper team")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def link(self, interaction: discord.Interaction, member: discord.Member, team: str) -> None:
        await interaction.response.defer(ephemeral=True)
        teams = await self._league_teams()
        if not teams:
            await interaction.followup.send("Run `/setup` first.")
            return
        match = next((item for item in teams if str(item["roster_id"]) == team), None) or next(
            (item for item in teams if item["team_name"].lower() == team.strip().lower()), None
        )
        if match is None:
            await interaction.followup.send("Choose the team from the list that appears as you type.")
            return
        await self.bot.db.link_member(match["roster_id"], member.id, match["team_name"], match["sleeper_user_id"])
        total = len(await self.bot.db.members())
        await interaction.followup.send(
            f"Linked {member.mention} to **{match['team_name']}**. {total} of {len(teams)} teams are linked.",
            allowed_mentions=render.QUIET,
        )

    @link.autocomplete("team")
    async def _team_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            teams = await self._league_teams()
        except Exception:
            return []
        typed = current.strip().lower()
        linked = {member["roster_id"] for member in await self.bot.db.members()}
        choices = []
        for team in teams:
            label = team["team_name"] if team["team_name"] == team["handle"] else f"{team['team_name']} ({team['handle']})"
            if typed in label.lower():
                suffix = " · linked" if team["roster_id"] in linked else ""
                choices.append(app_commands.Choice(name=(label + suffix)[:100], value=str(team["roster_id"])))
        return choices[:25]

    @app_commands.command(name="unlink", description="Remove a Discord member's link to their team")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def unlink(self, interaction: discord.Interaction, member: discord.Member) -> None:
        removed = await self.bot.db.unlink_member(member.id)
        text = f"Unlinked {member.mention}." if removed else f"{member.mention} was not linked to a team."
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=render.QUIET)

    @app_commands.command(name="members", description="Show which Sleeper teams are linked to which Discord members")
    @app_commands.guild_only()
    async def members(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        teams = await self._league_teams()
        if not teams:
            await interaction.followup.send("The league is not connected yet. The commissioner runs `/setup`.")
            return
        linked = {member["roster_id"]: member["discord_id"] for member in await self.bot.db.members()}
        lines = [
            f"**{team['team_name']}** · {render.mention(linked[team['roster_id']]) if team['roster_id'] in linked else '*not linked*'}"
            for team in teams
        ]
        embed = discord.Embed(title=await self.bot.db.get_state("league_name") or "League", description="\n".join(lines))
        embed.set_footer(text=f"{len(linked)} of {len(teams)} teams linked")
        await interaction.followup.send(embed=embed)


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Setup(bot))
