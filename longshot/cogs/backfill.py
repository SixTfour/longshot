"""/backfill: record a pick after the fact, for a game that has already kicked off.

For weeks the parlay was run by hand before the bot, or a pick that was never
entered. The odds feed has no lines for games already played, so the
commissioner types the line and price from the bet slip. ESPN still has the
result, so the leg is graded automatically.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from .. import render, tracking, weekly
from ..bot import Longshot, commissioner_only
from ..clients.espn import Athlete, EspnError, Game
from ..db import iso, utcnow
from ..domain.cards import card_title, split_week
from ..domain.grading import LOST, PENDING, PUSH, REVIEW, VOID, WON
from ..domain.lines import pick_label, typed_line
from ..domain.markets import PROP_MARKETS
from ..domain.odds_math import format_american, parse_american
from ..domain.schedule import kickoff_text
from ..subjects import Subject, bets, pick_row, resolve, resolve_bet, search, subjects_for

log = logging.getLogger(__name__)

Choice = app_commands.Choice[str]

AUTOCOMPLETE_BUDGET = 2.0  # Discord allows three seconds to answer
LIVE_BOX_TTL = timedelta(minutes=10)  # a finished game's box score is kept for good; a live one briefly
NOTHING = "-"

_OUTCOME = {
    WON: "It **won**.",
    LOST: "It **lost**.",
    PUSH: "It was a **push**, so it drops out of the parlay.",
    VOID: "It is **void** (the player did not play), so it drops out of the parlay.",
    REVIEW: "I could not grade it. Settle it with `/grade`.",
    PENDING: "The game is still being played, so it will be graded when it ends.",
}


def notice(text: str) -> list[Choice]:
    return [Choice(name=text[:100], value=NOTHING)]


class Backfill(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot
        self._loading: dict[str, asyncio.Task] = {}

    # --- what can be backfilled ---------------------------------------------------

    async def _started_games(self, week: int, *, fresh: bool = False) -> list[Game]:
        """This season's games in a week that have already kicked off."""
        now = utcnow()
        games = await self.bot.nfl.games(await self.bot.nfl.season(), week, fresh=fresh)
        return [game for game in games if game.state != "pre" or (game.time_set and game.kickoff <= now)]

    @staticmethod
    def _find_game(games: list[Game], value: str) -> Game | None:
        typed = value.strip().upper()
        return next((game for game in games if game.espn_id == value or game.label.upper() == typed), None)

    async def _box_players(self, game: Game) -> list[Athlete]:
        """Everyone with a stat line in the game. They are found by the same ids the grader uses."""
        key = f"espn:box:{game.espn_id}"
        cached = await self.bot.db.cache_get(key)
        if cached and (cached[0]["final"] or utcnow() - cached[1] < LIVE_BOX_TTL):
            return [Athlete(**item) for item in cached[0]["players"]]
        box = await self.bot.espn.boxscore(game.espn_id)
        players = [Athlete(athlete_id, line.name, "", line.team) for athlete_id, line in box.items()]
        await self.bot.db.cache_put(key, {"final": game.final, "players": [player.__dict__ for player in players]})
        return players

    async def _people(self, game: Game) -> list[Subject]:
        """The two teams, and every player who could have had a bet: the rosters plus the box score."""
        await self.bot.nfl.warm([game])
        listed = {athlete.id: athlete for athlete in self.bot.nfl.athletes([game])}
        try:
            for player in await self._box_players(game):
                listed.setdefault(player.id, player)  # a roster entry also knows the position
        except EspnError as error:
            log.warning("Could not read the box score for %s: %s", game.label, error)
        return subjects_for([game], listed.values())

    async def _people_soon(self, game: Game) -> list[Subject] | None:
        """_people within the autocomplete time limit, or None while it is still loading."""
        task = self._loading.get(game.espn_id)
        if task is None:
            task = self._loading[game.espn_id] = asyncio.create_task(self._people(game))
            task.add_done_callback(lambda done: self._loading.pop(game.espn_id, None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), AUTOCOMPLETE_BUDGET)
        except asyncio.TimeoutError:
            return None

    # --- autocomplete -------------------------------------------------------------

    async def game_choices(self, week: int | None, current: str) -> list[Choice]:
        if not week:
            return notice("Choose the week first.")
        games = await self._started_games(week)
        if not games:
            return notice(f"No Week {week} game has kicked off yet.")
        typed = current.strip().lower()
        choices = []
        for game in games:
            score = f" · {game.away_score}-{game.home_score}" if game.state != "pre" else ""
            status = "" if game.final or game.state == "pre" else " (live)"
            label = f"{game.label} · {kickoff_text(game.kickoff)}{score}{status}"
            if typed in label.lower():
                choices.append(Choice(name=label[:100], value=game.espn_id))
        return choices[:25]

    async def who_choices(self, week: int | None, game_value: str, current: str) -> list[Choice]:
        game = self._find_game(await self._started_games(week), game_value or "") if week else None
        if game is None:
            return notice("Choose the game first.")
        people = await self._people_soon(game)
        if people is None:
            return notice("Loading the players… keep typing.")
        return [Choice(name=subject.label[:100], value=subject.value) for subject in search(people, current)[:25]]

    async def bet_choices(self, week: int | None, game_value: str, who: str, current: str) -> list[Choice]:
        game = self._find_game(await self._started_games(week), game_value or "") if week else None
        people = await self._people_soon(game) if game else None
        subject = resolve(people, who or "") if people else None
        if subject is None:
            return notice("Choose the game and who first.")
        typed = current.strip().lower()
        return [Choice(name=label, value=key) for key, label in bets(subject) if typed in label.lower()][:25]

    @staticmethod
    def line_choices(bet: str, current: str) -> list[Choice]:
        """Show how what has been typed will be read, since there is no list of lines to choose from."""
        prop = PROP_MARKETS.get(bet)
        if bet == "h2h" or (prop and prop.yes_only):
            return notice("This bet has no line. Leave this empty.")
        if bet not in ("spreads", "totals") and prop is None:
            return notice("Choose the bet first.")
        if not current.strip():
            hint = {"spreads": "-2.5 or +3", "totals": "over 46.5 or under 46.5"}.get(bet, "over 74.5, under 74.5 or 75+")
            return notice(f"Type the line from the bet slip: {hint}")
        try:
            line = typed_line(bet, current, 100, team_abbr="Team", prop=prop)
        except ValueError as error:
            return notice(str(error).replace("`", ""))
        reading = pick_label(line, game="Game", player="Player" if prop else None, noun=prop.noun if prop else None)
        return [Choice(name=f"Reads as: {reading}"[:100], value=current.strip()[:100])]

    # --- recording a pick ---------------------------------------------------------

    async def record(
        self, entered_by: int, target_id: int, week: int, game_value: str, who: str, bet: str, odds: str,
        line: str | None = None,
    ) -> str:
        """Save a pick on a game already under way or finished, grade it, and say what happened."""
        bot = self.bot
        now = utcnow()
        target = render.mention(target_id)
        if await bot.db.member(target_id) is None:
            return f"{target} is not linked to a Sleeper team. Link them with `/link` first."
        try:
            price = parse_american(odds)
        except ValueError as error:
            return f"{error} Nothing was saved."

        season = await bot.nfl.season()
        schedule = await bot.nfl.games(season, week, fresh=True)
        game = self._find_game(schedule, game_value)
        if game is None:
            return "Choose the game from the list that appears for that week. Nothing was saved."
        if game.state == "pre" and not (game.time_set and game.kickoff <= now):
            return (
                f"{game.label} has not kicked off, so it is not a backfill. Use `/pickfor` while its parlay is "
                "taking picks. Nothing was saved."
            )

        subject = resolve(await self._people(game), who)
        if subject is None:
            return f"I could not find that player or team in {game.label}. Choose from the list. Nothing was saved."
        bet_key = resolve_bet(subject, bet)
        if bet_key is None:
            return f"Choose the bet for {subject.name} from the list. Nothing was saved."
        try:
            chosen = typed_line(
                bet_key, line, price, team_abbr=subject.team.abbr,
                is_home=subject.team.abbr == game.home.abbr, prop=PROP_MARKETS.get(bet_key),
            )
        except ValueError as error:
            return f"{error} Nothing was saved."

        # The game decides the parlay, as it does for /pick: only Thanksgiving week has two.
        card = next(name for name, games in split_week(schedule).items() if game in games)
        key = (season, week, card)
        row = await bot.db.ensure_parlay(*key)
        if not row["announced_at"] or row["skipped"]:
            # The bot never ran this parlay. It is recorded as closed and is never posted about.
            parlay = await bot.nfl.parlay(*key)
            await bot.db.update_parlay(
                *key, backfilled=1, skipped=0, stake=row["stake"] or await bot.settings.stake(),
                announced_at=row["announced_at"] or iso(now), closed_at=row["closed_at"] or iso(now),
                deadline_at=row["deadline_at"] or (iso(parlay.times.deadline_at) if parlay else None),
            )
            row = await bot.db.parlay(*key)
        # Quiet for a parlay entered after the fact, or one whose result is already posted.
        silent = bool(row["backfilled"]) or bool(row["recap_at"])

        pick_id, replaced = await bot.db.save_pick(pick_row(key, target_id, subject, chosen, entered_by))
        entered = await bot.db.pick(pick_id)
        channel = None if silent else await bot.league_channel()
        if channel is not None:
            # A leg added to a parlay still in play is something the league should see.
            await channel.send(
                f"📝 The commissioner entered a pick for {target}: **{entered['label']}** "
                f"({format_american(price)}) · {game.label}",
                allowed_mentions=render.QUIET,
            )
        settled = await tracking.settle_legs(bot, game, [entered])
        await tracking.announce_grades(bot, *key, settled, quiet=silent)
        if silent:
            # Work the parlay's recorded result out again with this leg in it.
            await bot.db.update_parlay(*key, recap_at=None, result=None, payout=None)
            await weekly.recap(bot, *key, quiet=True)

        saved = await bot.db.pick(pick_id)
        detail = render.outcome(saved)
        return (
            f"Recorded {target}'s {card_title(week, card)} pick: **{saved['label']}** "
            f"({format_american(price)}) · {game.label}. {_OUTCOME[saved['status']]}"
            + (f" ({detail}.)" if detail and saved["status"] in (WON, LOST) else "")
            + (" It replaces the pick they had." if replaced else "")
            + (" Nothing was posted in the channel." if silent else "")
        )

    @app_commands.command(name="backfill", description="Record a pick after the fact, for a game already played")
    @app_commands.describe(
        member="Whose pick it was",
        week="The week of this season",
        game="A game that has kicked off",
        who="A player or a team in that game",
        bet="The kind of bet",
        odds="The price on the bet slip, in American odds, such as +105",
        line="The line on the bet slip, such as over 74.5, 75+ or -2.5. Not needed for moneyline or anytime TD.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def backfill(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        week: app_commands.Range[int, 1, 18],
        game: str,
        who: str,
        bet: str,
        odds: str,
        line: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        text = await self.record(interaction.user.id, member.id, week, game, who, bet, odds, line)
        await interaction.followup.send(text, allowed_mentions=render.QUIET)

    @backfill.autocomplete("game")
    async def _game(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self.game_choices(interaction.namespace.week, current)

    @backfill.autocomplete("who")
    async def _who(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self.who_choices(interaction.namespace.week, interaction.namespace.game, current)

    @backfill.autocomplete("bet")
    async def _bet(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        space = interaction.namespace
        return await self.bet_choices(space.week, space.game, space.who, current)

    @backfill.autocomplete("line")
    async def _line(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return self.line_choices(interaction.namespace.bet or "", current)


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Backfill(bot))
