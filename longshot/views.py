"""Buttons that keep working after the bot restarts."""

from __future__ import annotations

import re

import discord

from .domain.odds_math import format_money


class PaidButton(discord.ui.DynamicItem[discord.ui.Button], template=r"longshot:paid:(?P<season>\d+):(?P<week>\d+)"):
    """'Mark paid' under the loser announcement. Only the commissioner can press it."""

    def __init__(self, season: int, week: int):
        super().__init__(
            discord.ui.Button(
                label="Mark paid",
                style=discord.ButtonStyle.success,
                custom_id=f"longshot:paid:{season}:{week}",
            )
        )
        self.season = season
        self.week = week

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["season"]), int(match["week"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not await bot.is_commissioner(interaction):
            await interaction.response.send_message("Only the commissioner can mark a debt as paid.", ephemeral=True)
            return
        newly_paid = await bot.db.mark_paid(self.season, self.week)
        debt = await bot.db.debt(self.season, self.week)
        if debt is None:
            await interaction.response.send_message("There is no debt recorded for that week.", ephemeral=True)
            return
        await interaction.response.edit_message(view=paid_view(self.season, self.week, paid=True))
        text = (
            f"Recorded: **{debt['team_name']}** paid {format_money(debt['amount'])} for Week {self.week}."
            if newly_paid
            else "That debt was already marked as paid."
        )
        await interaction.followup.send(text, ephemeral=True)


def paid_view(season: int, week: int, *, paid: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if paid:
        view.add_item(discord.ui.Button(label="Paid", style=discord.ButtonStyle.secondary, disabled=True, emoji="✅"))
    else:
        view.add_item(PaidButton(season, week))
    return view
