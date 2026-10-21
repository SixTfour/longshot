"""/slip, /placed and /grade: the parlays and their results."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, seasons, slipboard, tracking
from ..bot import Longshot, commissioner_only
from ..clients.espn import EspnError
from ..db import iso, utcnow
from ..domain.cards import BONUS, MAIN, card_title
from ..domain.grading import INJURED_NOTE, LOST, PENDING, PUSH, VOID, WON
from ..domain.injuries import INJURED, NOT_INJURED
from ..domain.odds_math import american_to_decimal, format_american, format_money, parse_american, total_return
from ..domain.schedule import stamp

log = logging.getLogger(__name__)

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

ESPN_DECIDES = "espn"
RULINGS = [
    app_commands.Choice(name="Injured: a miss is void", value=INJURED),
    app_commands.Choice(name="Not injured: a miss counts, whatever ESPN shows", value=NOT_INJURED),
    app_commands.Choice(name="Let ESPN decide (the default)", value=ESPN_DECIDES),
]


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
        names = await self.bot.team_names()
        this_season = await self.bot.nfl.season()
        embeds = []
        for row in rows:
            key = (row["season"], row["week"], row["card"])
            picks = await self.bot.db.parlay_picks(*key)
            # A slip from an earlier year says which year.
            title = f"{row['season']} · {card_title(row['week'], row['card'])} parlay"
            games = {game.espn_id: game for game in await self.bot.nfl.games(row["season"], row["week"])}
            embed = render.slip_embed(
                row, picks, row["stake"] or await self.bot.settings.stake(), names,
                title=title if row["season"] != this_season else None, games=games,
            )
            if not row["closed_at"]:
                picked = {pick["discord_id"] for pick in picks}
                waiting = [discord_id for discord_id in members if discord_id not in picked]
                if waiting:
                    embed.add_field(name="Waiting on", value=render.teams(waiting, names), inline=False)
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

    async def _member_pick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        week: int | None,
        parlay: app_commands.Choice[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The member's pick and its parlay, or None after telling the commissioner why not."""
        picks = []
        for row in await self._parlays(week, parlay.value if parlay else None):
            pick = await self.bot.db.member_pick(row["season"], row["week"], row["card"], member.id)
            if pick is not None:
                picks.append((row, pick))
        if not picks:
            await interaction.followup.send(f"{member.mention} has no pick for that week.", allowed_mentions=render.QUIET)
            return None
        if len(picks) > 1:
            await interaction.followup.send(WHICH)
            return None
        return picks[0]

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
        found = await self._member_pick(interaction, member, week, parlay)
        if found is None:
            return
        row, pick = found
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
        await slipboard.refresh(self.bot, *key)
        text = (
            "will be graded by the bot again on its next check"
            if status == PENDING
            else f"is settled as **{result.name.split(' (')[0].lower()}**"
        )
        await interaction.followup.send(
            f"{member.mention}'s {card_title(row['week'], row['card'])} leg ({render.leg(pick)}) {text}.",
            allowed_mentions=render.QUIET,
        )


    @app_commands.command(name="injury", description="Rule whether a player got hurt, for the injury exemption")
    @app_commands.describe(
        member="Whose leg it is",
        status="Injured voids a miss. ESPN's play-by-play decides unless you rule.",
        week="The week. Leave empty for this week.",
        parlay="Only needed in Thanksgiving week, when there are two",
    )
    @app_commands.choices(status=RULINGS, parlay=PARLAYS)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def injury(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        status: app_commands.Choice[str],
        week: app_commands.Range[int, 1, 18] | None = None,
        parlay: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        found = await self._member_pick(interaction, member, week, parlay)
        if found is None:
            return
        row, pick = found
        key = (row["season"], row["week"], row["card"])
        who = f"{member.mention}'s {card_title(row['week'], row['card'])} leg ({render.leg(pick)})"
        if pick["player_id"] is None:
            await interaction.followup.send(
                f"{who} is a team bet. The injury exemption is only for player props.", allowed_mentions=render.QUIET
            )
            return

        ruling = None if status.value == ESPN_DECIDES else status.value
        await self.bot.db.set_injury(pick["id"], ruling)
        if pick["status"] != PENDING and pick["graded_by"] != "auto":
            await interaction.followup.send(
                f"Saved. {who} was settled by hand with `/grade`, and that stands. To have the bot grade it "
                "again with this ruling, use `/grade` with **Undo**.",
                allowed_mentions=render.QUIET,
            )
            return

        # Grade it again now, with the ruling.
        before = pick["status"]
        if before != PENDING:
            await self.bot.db.grade_pick(pick["id"], PENDING, pick["stat_value"], None, "auto")
        settled: list[int] = []
        games = {game.espn_id: game for game in await self.bot.nfl.games(row["season"], row["week"], fresh=True)}
        game = games.get(pick["espn_event_id"])
        if game is not None and (game.state != "pre" or game.called_off):
            try:
                settled = await tracking.settle_legs(self.bot, game, [await self.bot.db.pick(pick["id"])], now=utcnow())
            except EspnError as error:
                # Left pending: the tracker grades it on its next check.
                log.warning("Could not regrade %s after an injury ruling: %s", game.label, error)
        after = await self.bot.db.pick(pick["id"])
        if after["status"] != before:
            # A parlay already summed up is reopened, so the corrected result is posted once every leg is settled.
            if row["recap_at"]:
                await self.bot.db.update_parlay(*key, recap_at=None, result=None, payout=None)
            await tracking.announce_grades(self.bot, *key, settled)
        await slipboard.refresh(self.bot, *key)
        await interaction.followup.send(
            f"Saved. {who} {self._injury_effect(after, ruling)}", allowed_mentions=render.QUIET
        )

    @staticmethod
    def _injury_effect(pick: dict[str, Any], ruling: str | None) -> str:
        """What the ruling did to the leg, as the end of a sentence."""
        status = pick["status"]
        if status == VOID and pick["note"] == INJURED_NOTE:
            return "is **void**: the player got hurt, so it drops out of the parlay."
        if status == PENDING and pick["held_since"]:
            return "missed, and ESPN shows the player was hurt but came back. It is on hold until the injury hold runs out."
        if status == PENDING:
            return {
                INJURED: "is still being played. If it misses, it is void.",
                NOT_INJURED: "is still being played. If it misses, it counts as a miss, whatever ESPN shows.",
            }.get(ruling, "is still being played. ESPN's play-by-play decides whether a miss is void.")
        if status == WON:
            return "hit, so it stands. The injury exemption only voids a miss."
        if status == LOST and pick["side"] == "under":
            return "went over, so it stays a miss. An under is never voided for an injury."
        if status == LOST:
            return "counts as a **miss**." if ruling == NOT_INJURED else (
                "counts as a **miss**: ESPN shows no injury that voids it. Rule it **Injured** to void it."
            )
        return f"is settled as **{status}**{f' ({note})' if (note := pick['note']) else ''}."


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Slip(bot))
