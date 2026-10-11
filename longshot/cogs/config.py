"""/config: league settings the commissioner changes from Discord, with no restart."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import Longshot, commissioner_only
from ..db import utcnow
from ..domain.schedule import stamp
from ..settings import KNOWN_SPORTSBOOKS, SPECS

SETTING_CHOICES = [app_commands.Choice(name=spec.label, value=key) for key, spec in SPECS.items()]


class ConfigCog(commands.Cog, name="Config"):
    def __init__(self, bot: Longshot):
        self.bot = bot

    async def _overview(self) -> discord.Embed:
        embed = discord.Embed(title="Longshot settings")
        for key, spec in SPECS.items():
            embed.add_field(
                name=spec.label, value=f"**{await self.bot.settings.describe(key)}**\n{spec.hint}", inline=False
            )
        embed.set_footer(text="Change one with /config setting:<name> value:<new value>. Times are US Eastern.")
        return embed

    async def _effect(self, key: str) -> str:
        """What a change means for the week in progress."""
        if key not in ("deadline", "announce", "reminders"):
            if key == "sportsbook":
                return "New lookups use it from now on. Picks already made keep the price they were made at."
            return ""
        now = utcnow()
        active = await self.bot.nfl.active(now)
        if active is None:
            return "It applies from the next week of the season."
        row = await self.bot.db.week(active.season, active.week)
        if key == "deadline":
            if row and row["closed_at"]:
                return f"Week {active.week} picks are already closed, so it applies from next week."
            note = " (moved up, because that week's first game kicks off before it)" if active.times.moved_early else ""
            return f"Week {active.week} picks now close {stamp(active.times.deadline_at, 'F')}{note}."
        return f"It applies from Week {active.week + 1}." if row and row["announced_at"] else ""

    @app_commands.command(name="config", description="Show or change league settings (deadline, sportsbook, and more)")
    @app_commands.describe(setting="The setting to change", value="Its new value")
    @app_commands.choices(setting=SETTING_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def config(
        self, interaction: discord.Interaction, setting: app_commands.Choice[str] | None = None, value: str | None = None
    ) -> None:
        if setting is None:
            await interaction.response.send_message(embed=await self._overview(), ephemeral=True)
            return
        key, spec = setting.value, SPECS[setting.value]
        if value is None:
            await interaction.response.send_message(
                f"**{spec.label}** is {await self.bot.settings.describe(key)}.\n{spec.hint}", ephemeral=True
            )
            return
        try:
            await self.bot.settings.set(key, value)
        except ValueError as error:
            await interaction.response.send_message(f"Not changed. {error}", ephemeral=True)
            return

        text = f"**{spec.label}** is now {await self.bot.settings.describe(key)}."
        effect = await self._effect(key)
        if key == "sportsbook" and (await self.bot.settings.sportsbook()) not in KNOWN_SPORTSBOOKS:
            effect += " I do not recognise that key; if no lines appear in `/pick`, check its spelling."
        # Announced in the open: members need to know when a deadline moves.
        await interaction.response.send_message(f"{text} {effect}".strip())

    @config.autocomplete("value")
    async def _value_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.namespace.setting != "sportsbook":
            return []
        typed = current.strip().lower()
        return [
            app_commands.Choice(name=f"{name} ({key})", value=key)
            for key, name in KNOWN_SPORTSBOOKS.items()
            if typed in key or typed in name.lower()
        ][:25]


async def setup(bot: Longshot) -> None:
    await bot.add_cog(ConfigCog(bot))
