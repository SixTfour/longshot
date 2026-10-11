"""/pick, /pickfor and /mypick.

Members are expected to have found their bet in the sportsbook app already, so
/pick is a lookup: who (a player or a team), which bet, which line.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
import discord
from discord import app_commands
from discord.ext import commands

from .. import render
from ..bot import Longshot, commissioner_only
from ..clients.espn import Athlete, Game, Team
from ..clients.odds import OddsApiError
from ..db import parse_iso, utcnow
from ..domain.lines import Line, conflict, filter_lines, find_line, pick_label
from ..domain.markets import PROP_MARKETS, TEAM_BETS, props_for_position
from ..domain.names import normalize
from ..domain.odds_math import format_american
from ..domain.schedule import kickoff_text, stamp
from ..linebook import NoCredits
from ..nfl import Parlay, WeekPlan

log = logging.getLogger(__name__)

Choice = app_commands.Choice[str]

# Discord allows three seconds to answer an autocomplete; leave room for the reply itself.
AUTOCOMPLETE_BUDGET = 2.0
NOTHING = "-"  # the value behind a list entry that is a notice, not a choice


def notice(text: str) -> list[Choice]:
    return [Choice(name=text[:100], value=NOTHING)]


@dataclass(frozen=True)
class Subject:
    """Who a pick is about: a team, or a player on a team, in one game."""

    game: Game
    team: Team
    athlete: Athlete | None = None

    @property
    def name(self) -> str:
        return self.athlete.name if self.athlete else self.team.name

    @property
    def value(self) -> str:
        return f"p:{self.athlete.id}" if self.athlete else f"t:{self.team.abbr}"

    @property
    def label(self) -> str:
        when = f"{self.game.label} {kickoff_text(self.game.kickoff)}"
        if self.athlete:
            return f"{self.athlete.name} · {self.athlete.position} · {when}"
        return f"{self.team.name} · {when}"


@dataclass(frozen=True)
class Window:
    """What can be picked at this moment."""

    plan: WeekPlan | None
    taking: list[tuple[Parlay, list[Game]]]  # parlays open to picks, each with its pickable games
    reason: str | None  # why nothing can be picked, when no parlay is open

    @property
    def games(self) -> list[Game]:
        return [game for _, games in self.taking for game in games]

    def parlay_for(self, game: Game) -> Parlay | None:
        return next((parlay for parlay, games in self.taking if game in games), None)


class Picks(commands.Cog):
    def __init__(self, bot: Longshot):
        self.bot = bot
        self._warming: asyncio.Task | None = None  # held so the task is not garbage-collected

    # --- what can be picked right now -------------------------------------------

    async def _window(self, late: bool) -> Window:
        """The live week, and which of its parlays are taking picks right now.

        late is the commissioner's /pickfor: it can add to a parlay whose deadline
        has passed, but only from the games still left in that parlay's window.
        """
        now = utcnow()
        plan = await self.bot.nfl.active(now)
        if plan is None:
            return Window(None, [], "Picks are not open right now.")
        taking: list[tuple[Parlay, list[Game]]] = []
        closed: list[str] = []
        for parlay in plan.parlays:
            row = await self.bot.db.parlay(*parlay.key)
            if row is None or not row["announced_at"]:
                continue
            if not late and (row["closed_at"] or now >= parlay.times.deadline_at):
                closed.append(parlay.title)
            else:
                taking.append((parlay, parlay.pickable(now)))
        if taking:
            return Window(plan, taking, None)
        reason = f"{' and '.join(closed)} picks are closed." if closed else "Picks are not open right now."
        return Window(plan, [], reason)

    def _subjects(self, games: list[Game]) -> list[Subject]:
        teams = [Subject(game, team) for game in games for team in (game.away, game.home)]
        by_abbr = {subject.team.abbr: subject for subject in teams}
        players = [
            Subject(by_abbr[athlete.team].game, by_abbr[athlete.team].team, athlete)
            for athlete in self.bot.nfl.athletes(games)
            if athlete.team in by_abbr
        ]
        return teams + players

    def _search(self, games: list[Game], typed: str) -> list[Subject]:
        """Teams and players matching what has been typed, best match first."""
        needle = normalize(typed)
        subjects = self._subjects(games)
        if not needle:
            return [subject for subject in subjects if subject.athlete is None]

        ranked = []
        for subject in subjects:
            name = normalize(subject.name)
            if name.startswith(needle) or (subject.athlete is None and subject.team.abbr.lower() == needle):
                score = 0
            elif any(word.startswith(needle) for word in name.split()):
                score = 1
            elif needle in name:
                score = 2
            else:
                continue
            ranked.append((score, subject.athlete is not None, subject.name, subject))
        return [entry[3] for entry in sorted(ranked, key=lambda entry: entry[:3])]

    def _resolve(self, games: list[Game], value: str) -> Subject | None:
        """Turn the 'who' field back into a subject: a value from the list, or an exact name typed out."""
        subjects = self._subjects(games)
        for subject in subjects:
            if subject.value == value:
                return subject
        wanted = normalize(value)
        exact = [subject for subject in subjects if normalize(subject.name) == wanted]
        return exact[0] if len(exact) == 1 else None

    @staticmethod
    def _bets(subject: Subject) -> list[tuple[str, str]]:
        if subject.athlete is None:
            return list(TEAM_BETS.items())
        return [(market.key, market.label) for market in props_for_position(subject.athlete.position)]

    def _resolve_bet(self, subject: Subject, value: str) -> str | None:
        for key, label in self._bets(subject):
            if value == key or value.strip().lower() == label.lower():
                return key
        return None

    async def _lines(self, subject: Subject, bet: str) -> list[Line]:
        if subject.athlete is None:
            return await self.bot.linebook.team_lines(subject.game, subject.team.abbr, bet)
        return await self.bot.linebook.player_lines(subject.game, subject.athlete.name, PROP_MARKETS[bet])

    # --- autocomplete -----------------------------------------------------------

    async def _who_choices(self, current: str, *, late: bool) -> list[Choice]:
        window = await self._window(late)
        if window.reason:
            return notice(window.reason)
        games = window.games
        if not games:
            return notice("No games left to pick from this week.")
        if not self.bot.nfl.warmed(games):
            # First use since a restart: load rosters in the background; teams work meanwhile.
            self._warming = asyncio.create_task(self.bot.nfl.warm(games))
        return [Choice(name=subject.label[:100], value=subject.value) for subject in self._search(games, current)[:25]]

    async def _bet_choices(self, interaction: discord.Interaction, current: str, *, late: bool) -> list[Choice]:
        games = (await self._window(late)).games
        subject = self._resolve(games, interaction.namespace.who or "")
        if subject is None:
            return notice("Choose a player or team first.")
        typed = current.strip().lower()
        return [Choice(name=label, value=key) for key, label in self._bets(subject) if typed in label.lower()][:25]

    async def _line_choices(self, interaction: discord.Interaction, current: str, *, late: bool) -> list[Choice]:
        games = (await self._window(late)).games
        subject = self._resolve(games, interaction.namespace.who or "")
        bet = self._resolve_bet(subject, interaction.namespace.bet or "") if subject else None
        if subject is None or bet is None:
            return notice("Choose who and the bet first.")
        book = await self.bot.settings.sportsbook_name()
        try:
            lines = await asyncio.wait_for(self._lines(subject, bet), AUTOCOMPLETE_BUDGET)
        except asyncio.TimeoutError:
            # The lookup carries on in the background; the next keystroke will find it cached.
            return notice(f"Fetching {book} lines… keep typing.")
        except NoCredits:
            return notice("The odds feed is out of credits for now. Ask the commissioner.")
        except OddsApiError as error:
            log.warning("Line lookup failed: %s", error)
            return notice("The odds feed did not respond. Try again in a moment.")
        if not lines:
            return notice(f"{book} has no line for this bet right now.")
        matches = filter_lines(lines, current)
        if not matches:
            return notice("No line matches that. Clear the field to see them all.")
        return [Choice(name=line.choice[:100], value=line.key) for line in matches[:25]]

    # --- placing a pick ---------------------------------------------------------

    async def _place(
        self, interaction: discord.Interaction, target: discord.abc.User, who: str, bet: str, line: str, *, late: bool
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        bot = self.bot
        say = interaction.followup.send
        yours = target.id == interaction.user.id

        member = await bot.db.member(target.id)
        if member is None:
            who_text = "You are" if yours else f"{target.mention} is"
            await say(f"{who_text} not linked to a Sleeper team yet. The commissioner does that with `/link`.")
            return

        window = await self._window(late)
        if window.reason:
            extra = "" if late else " Ask the commissioner about a late pick."
            await say(window.reason + extra)
            return
        games = window.games

        await bot.nfl.warm(games)
        subject = self._resolve(games, who)
        if subject is None:
            await say(
                "I could not find that player or team in a game that can still be picked. "
                "Start typing a name in `who` and choose from the list."
            )
            return
        bet_key = self._resolve_bet(subject, bet)
        if bet_key is None:
            await say(f"Choose the bet for {subject.name} from the list.")
            return

        book = await bot.settings.sportsbook_name()
        bet_label = TEAM_BETS.get(bet_key) or PROP_MARKETS[bet_key].label
        lines = await self._lines(subject, bet_key)
        if not lines:
            await say(
                f"{book} has no **{bet_label}** line for **{subject.name}** right now, so nothing was saved. "
                "Choose another bet."
            )
            return
        chosen = find_line(lines, line)
        if chosen is None:
            await say(
                "That line is not on the list. Lines move, so run the command again and choose from the list "
                "that appears. Nothing was saved."
            )
            return

        game = subject.game
        parlay = window.parlay_for(game)  # the game decides which parlay the pick joins
        prop = PROP_MARKETS.get(bet_key)
        pick = {
            "season": parlay.season,
            "week": parlay.week,
            "card": parlay.card,
            "discord_id": target.id,
            "espn_event_id": game.espn_id,
            "game": game.label,
            "kickoff": game.kickoff.isoformat(timespec="seconds"),
            "home_abbr": game.home.abbr,
            "away_abbr": game.away.abbr,
            "market": chosen.market,
            "side": chosen.side,
            "line": chosen.point,
            "price": chosen.price,
            "alt": int(chosen.alt),
            "team_abbr": None if subject.athlete else subject.team.abbr,
            "player_id": subject.athlete.id if subject.athlete else None,
            "player_name": subject.athlete.name if subject.athlete else None,
            "label": pick_label(
                chosen, game=game.label,
                player=subject.athlete.name if subject.athlete else None,
                noun=prop.noun if prop else None,
            ),
            "entered_by": interaction.user.id,
        }

        for other in await bot.db.parlay_picks(*parlay.key):
            if other["discord_id"] == target.id:
                continue
            clash = conflict(pick, other)
            if clash == "same":
                await say(
                    f"{render.mention(other['discord_id'])} already has that exact leg. Nothing was saved; "
                    "choose something else.",
                    allowed_mentions=render.QUIET,
                )
                return
            if clash == "opposite":
                await say(
                    f"That is the direct opposite of {render.mention(other['discord_id'])}'s leg "
                    f"(**{other['label']}**), and both cannot be in one parlay. Nothing was saved.",
                    allowed_mentions=render.QUIET,
                )
                return

        _, replaced = await bot.db.save_pick(pick)
        price = format_american(chosen.price)
        when = f"{game.label}, {stamp(game.kickoff, 'f')}"
        owner = "Your" if yours else f"{target.mention}'s"
        await say(
            f"{owner} {parlay.title} pick is **{pick['label']}** ({price} at {book}) · {when}.",
            allowed_mentions=render.QUIET,
        )

        channel = await bot.league_channel()
        if channel is not None:
            total = len(await bot.db.members())
            count = len(await bot.db.parlay_picks(*parlay.key))
            tally = f"{count} of {total} picks in"
            if window.plan.split:
                tally = f"{parlay.title}: {tally}"
            if not yours:
                lead = f"📝 The commissioner entered a pick for {target.mention}:"
            elif replaced:
                lead = f"🔁 {target.mention} changed their pick to"
            else:
                lead = f"🔒 {target.mention} locked in"
            await channel.send(
                f"{lead} **{pick['label']}** ({price}) · {when} · {tally}",
                allowed_mentions=render.QUIET,
            )

    @app_commands.command(name="pick", description="Lock in your leg for this week's parlay")
    @app_commands.describe(
        who="A player or a team: start typing a name",
        bet="The kind of bet",
        line="The line and price, alternates included",
    )
    @app_commands.guild_only()
    async def pick(self, interaction: discord.Interaction, who: str, bet: str, line: str) -> None:
        await self._place(interaction, interaction.user, who, bet, line, late=False)

    @pick.autocomplete("who")
    async def _pick_who(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._who_choices(current, late=False)

    @pick.autocomplete("bet")
    async def _pick_bet(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._bet_choices(interaction, current, late=False)

    @pick.autocomplete("line")
    async def _pick_line(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._line_choices(interaction, current, late=False)

    @app_commands.command(name="pickfor", description="Enter or replace a member's pick, even after the deadline")
    @app_commands.describe(
        member="Whose pick this is",
        who="A player or a team: start typing a name",
        bet="The kind of bet",
        line="The line and price, alternates included",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @commissioner_only()
    async def pickfor(
        self, interaction: discord.Interaction, member: discord.Member, who: str, bet: str, line: str
    ) -> None:
        await self._place(interaction, member, who, bet, line, late=True)

    @pickfor.autocomplete("who")
    async def _pickfor_who(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._who_choices(current, late=True)

    @pickfor.autocomplete("bet")
    async def _pickfor_bet(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._bet_choices(interaction, current, late=True)

    @pickfor.autocomplete("line")
    async def _pickfor_line(self, interaction: discord.Interaction, current: str) -> list[Choice]:
        return await self._line_choices(interaction, current, late=True)

    @app_commands.command(name="mypick", description="Show your pick for this week")
    @app_commands.guild_only()
    async def mypick(self, interaction: discord.Interaction) -> None:
        now = utcnow()
        plan = await self.bot.nfl.active(now)
        lines = []
        for parlay in plan.parlays if plan else []:
            row = await self.bot.db.parlay(*parlay.key)
            if row is None or not row["announced_at"]:
                continue
            pick = await self.bot.db.member_pick(*parlay.key, interaction.user.id)
            if pick is not None:
                lines.append(
                    f"**{parlay.title}:** {render.slip_line(pick)}\n{pick['game']}, "
                    f"{stamp(parse_iso(pick['kickoff']), 'f')}"
                )
            elif row["closed_at"] or now >= parlay.times.deadline_at:
                lines.append(f"**{parlay.title}:** no pick, and picks are closed.")
            else:
                lines.append(
                    f"**{parlay.title}:** no pick yet. Picks close {stamp(parlay.times.deadline_at, 'R')}. Use `/pick`."
                )
        await interaction.response.send_message(
            "\n".join(lines) or "There is no parlay running right now.", ephemeral=True, allowed_mentions=render.QUIET
        )


async def setup(bot: Longshot) -> None:
    await bot.add_cog(Picks(bot))
