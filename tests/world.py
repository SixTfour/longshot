"""A stand-in for Discord, Sleeper, ESPN and the odds feed, so a whole week can be played out in a test.

The schedule is a real week of 2026 (Week 6, or Week 12 for Thanksgiving) and
the box score is a real one (Buccaneers at Cowboys), re-homed onto that week's
Buccaneers home game. The odds are hand-written in the shape The Odds API documents.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from conftest import load

from longshot.bot import Longshot
from longshot.clients.espn import (
    Athlete,
    Game,
    Scoreboard,
    SeasonCalendar,
    Team,
    parse_boxscore,
    parse_calendar,
    parse_game_roster,
)
from longshot.clients.odds import OddsApiError
from longshot.config import SETTING_DEFAULTS, Config
from longshot.domain.cards import MAIN
from longshot.domain.schedule import ET
from longshot.nfl import Nfl

SEASON, WEEK, THANKSGIVING_WEEK = 2026, 6, 12
TNF, BUCS_GAME = "SEA @ DEN", "PIT @ TB"  # Week 6: Thursday night, and Sunday early
TURKEY_GAME, MONDAY_BUCS = "CHI @ DET", "CAR @ TB"  # Week 12: Thanksgiving early, and Monday night
# Per week: the game with team lines, and the Buccaneers game with player props and a box score.
FEATURED = {WEEK: (TNF, BUCS_GAME), THANKSGIVING_WEEK: (TURKEY_GAME, MONDAY_BUCS)}
NAMES = {
    "SEA": "Seattle Seahawks", "DEN": "Denver Broncos", "PIT": "Pittsburgh Steelers", "TB": "Tampa Bay Buccaneers",
    "CHI": "Chicago Bears", "DET": "Detroit Lions", "CAR": "Carolina Panthers",
}
IRVING, DANIELS, BENCHED = "4596448", "4596472", "999001"


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


def _team(abbr: str) -> Team:
    return Team(abbr, abbr, NAMES.get(abbr, f"Team {abbr}"))


def week_games(week: int) -> dict[str, Game]:
    games = {}
    for row in load("espn_kickoffs_2026.json")[str(week)]:
        away, home = row["game"].replace(" VS ", " @ ").split(" @ ")
        games[row["game"].replace(" VS ", " @ ")] = Game(
            espn_id=row["espn_id"], season=SEASON, week=week,
            kickoff=datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")),
            home=_team(home), away=_team(away), state="pre", completed=False, status="STATUS_SCHEDULED",
            home_score=0, away_score=0,
        )
    return games


class FakeEspn:
    def __init__(self, week: int = WEEK):
        self.week = week
        self.team_game, self.bucs_game = FEATURED[week]
        self.games = week_games(week)
        self.box = parse_boxscore(load("espn_summary_401872980.json"))
        self.requests = 0

    def set(self, label: str, *, state: str, home: int = 0, away: int = 0, status: str | None = None) -> None:
        done = state == "post"
        status = status or {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL"}[state]
        self.games[label] = replace(
            self.games[label], state=state, completed=done and status == "STATUS_FINAL", status=status,
            home_score=home, away_score=away,
        )

    def finish_everything(self) -> None:
        for label, game in self.games.items():
            if game.state != "post":
                self.set(label, state="post", home=20, away=17)

    async def scoreboard(self, season: int, week: int) -> Scoreboard:
        self.requests += 1
        games = list(self.games.values()) if (season, week) == (SEASON, self.week) else []
        return Scoreboard(season, week, sorted(games, key=lambda g: g.kickoff))

    async def calendar(self) -> SeasonCalendar:
        """ESPN's real 2026 calendar, so the season starts and ends on the real dates."""
        return parse_calendar(load("espn_calendar_2026.json"))

    async def boxscore(self, event_id: str):
        game = next(g for g in self.games.values() if g.espn_id == event_id)
        return self.box if game.label == self.bucs_game and game.state != "pre" else {}

    async def team_roster(self, team_id: str) -> list[Athlete]:
        if team_id == "TB":
            return [
                Athlete(IRVING, "Bucky Irving", "RB", "TB"),
                Athlete(DANIELS, "Jalon Daniels", "QB", "TB"),
                Athlete(BENCHED, "Benny Benchwarmer", "WR", "TB"),
            ]
        return []

    async def played(self, game: Game) -> dict[str, bool]:
        return parse_game_roster(load("espn_game_roster.json")) | {BENCHED: False, IRVING: True}


def _outcome(name, price, point=None, description=None):
    data = {"name": name, "price": price}
    if point is not None:
        data["point"] = point
    if description:
        data["description"] = description
    return data


class FakeOdds:
    """Counts paid calls and reports a credit balance, like the real feed's response headers."""

    def __init__(self, espn: FakeEspn, on_usage):
        self._espn = espn
        self._on_usage = on_usage
        self.remaining = 480
        self.calls: list[str] = []
        self.fail = False

    def _teams(self) -> tuple[str, str]:
        """Home and away names in the game that has team lines. The home side is the 2.5-point favourite."""
        game = self._espn.games[self._espn.team_game]
        return game.home.name, game.away.name

    def _event(self, label: str) -> dict:
        game = self._espn.games[label]
        return {
            "id": f"odds-{game.espn_id}", "home_team": game.home.name, "away_team": game.away.name,
            "commence_time": game.kickoff.isoformat().replace("+00:00", "Z"),
        }

    async def _spend(self, name: str, cost: int) -> None:
        if self.fail:
            raise OddsApiError("The Odds API returned HTTP 500.", 500)
        self.calls.append(name)
        self.remaining -= cost
        await self._on_usage(self.remaining, 500 - self.remaining)

    async def events(self) -> list[dict]:
        await self._spend("events", 0)
        return [self._event(self._espn.team_game), self._event(self._espn.bucs_game)]

    async def main_markets(self, bookmaker: str) -> list[dict]:
        await self._spend("main", 3)
        home, away = self._teams()
        markets = [
            {"key": "h2h", "outcomes": [_outcome(home, -140), _outcome(away, 120)]},
            {"key": "spreads", "outcomes": [_outcome(home, -110, -2.5), _outcome(away, -110, 2.5)]},
            {"key": "totals", "outcomes": [_outcome("Over", -110, 43.5), _outcome("Under", -110, 43.5)]},
        ]
        return [self._event(self._espn.team_game) | {"bookmakers": [{"key": bookmaker, "markets": markets}]}]

    async def event_markets(self, event_id: str, markets, bookmaker: str) -> dict:
        await self._spend(f"event:{','.join(markets)}", len(markets))
        team_game = self._espn.team_game
        label = team_game if event_id == self._event(team_game)["id"] else self._espn.bucs_game
        home, away = self._teams()
        available = {
            "alternate_spreads": [_outcome(home, 150, -6.5), _outcome(away, -190, 6.5)],
            "alternate_totals": [_outcome("Over", 140, 47.5), _outcome("Under", -170, 47.5)],
            "player_rush_yds": [
                _outcome("Over", -115, 74.5, "Bucky Irving"), _outcome("Under", -105, 74.5, "Bucky Irving"),
                _outcome("Over", -110, 20.5, "Benny Benchwarmer"),
            ],
            "player_rush_yds_alternate": [
                _outcome("Over", 320, 149.5, "Bucky Irving"), _outcome("Over", 900, 174.5, "Bucky Irving"),
            ],
            "player_anytime_td": [_outcome("Yes", -130, description="Bucky Irving")],
        }
        found = [{"key": key, "outcomes": available[key]} for key in markets if key in available]
        if label == team_game:
            found = [market for market in found if market["key"].startswith("alternate")]
        else:
            found = [market for market in found if market["key"].startswith("player")]
        return self._event(label) | {"bookmakers": [{"key": bookmaker, "markets": found}] if found else []}


class FakeSleeper:
    def __init__(self):
        self.scores = {1: 121.5, 2: 78.32, 3: 99.0}
        self.season = str(SEASON)  # the season the connected league belongs to
        self.status = "in_season"
        # A typical league: 14 weeks, then three playoff rounds, ending in Week 17.
        self.settings = {"start_week": 1, "playoff_week_start": 15, "playoff_teams": 6, "playoff_round_type": 0}
        self.bracket = [{"r": 1, "m": 1}, {"r": 1, "m": 2}, {"r": 2, "m": 3}, {"r": 2, "m": 4}, {"r": 3, "m": 5}]

    async def league(self, league_id: str) -> dict:
        return {
            "league_id": league_id, "name": "Test League", "season": self.season, "sport": "nfl",
            "status": self.status, "settings": dict(self.settings),
        }

    async def winners_bracket(self, league_id: str) -> list[dict]:
        return list(self.bracket)

    async def teams(self, league_id: str) -> list[dict]:
        return [
            {"roster_id": n, "sleeper_user_id": f"u{n}", "team_name": f"Team {n}", "handle": f"user{n}"}
            for n in sorted(self.scores)
        ]

    async def matchups(self, league_id: str, week: int) -> list[dict]:
        return [{"roster_id": n, "points": points, "custom_points": None} for n, points in self.scores.items()]


class Recorder:
    """A channel or a user's DMs: remembers what was sent to it."""

    def __init__(self, mention: str = "#parlay"):
        self.mention = mention
        self.sent: list[SimpleNamespace] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(SimpleNamespace(content=content or "", **kwargs))

    @property
    def texts(self) -> list[str]:
        return [message.content for message in self.sent]

    def last(self) -> str:
        return self.sent[-1].content

    def clear(self) -> None:
        self.sent.clear()


def user(discord_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=discord_id, mention=f"<@{discord_id}>", display_name=f"user{discord_id}")


class FakeInteraction:
    """Just enough of discord.Interaction for the pick commands."""

    def __init__(self, bot: Longshot, discord_id: int, **namespace):
        self.client = bot
        self.user = user(discord_id)
        self.namespace = SimpleNamespace(**namespace)
        self.replies: list[str] = []
        self.response = SimpleNamespace(defer=self._defer, send_message=self._reply, is_done=lambda: True)
        self.followup = SimpleNamespace(send=self._reply)

    async def _defer(self, **kwargs):
        pass

    async def _reply(self, content=None, **kwargs):
        self.replies.append(content or "")

    @property
    def reply(self) -> str:
        return self.replies[-1]


class World:
    def __init__(self, bot: Longshot, espn: FakeEspn, odds: FakeOdds, sleeper: FakeSleeper, channel: Recorder):
        self.bot, self.espn, self.odds, self.sleeper, self.channel = bot, espn, odds, sleeper, channel
        self.dms: dict[int, Recorder] = {}
        self.picks = bot.get_cog("Picks")

    async def tick(self, now: datetime) -> None:
        self.bot.scheduler._last_track = None  # never throttle live tracking in a test
        await self.bot.scheduler.run_due(now)

    async def pick(self, discord_id: int, who: str, bet: str, line: str, *, by: int | None = None) -> str:
        interaction = FakeInteraction(self.bot, by or discord_id)
        await self.picks._place(interaction, user(discord_id), who, bet, line, late=by is not None)
        return interaction.reply

    async def parlay(self, card: str = MAIN) -> dict:
        return await self.bot.db.parlay(SEASON, self.espn.week, card)

    async def week(self) -> dict:
        """The week's main parlay."""
        return await self.parlay(MAIN)

    async def statuses(self, card: str = MAIN) -> dict[int, str]:
        picks = await self.bot.db.parlay_picks(SEASON, self.espn.week, card)
        return {pick["discord_id"]: pick["status"] for pick in picks}

    async def close(self) -> None:
        await self.bot.close()


async def build(tmp_path: Path, *, week: int = WEEK, members: int = 3, **settings: str) -> World:
    """A bot connected to the league, with members 1..n linked to rosters 1..n."""
    config = Config(
        discord_token="not-a-real-token", odds_api_key="not-a-real-key", db_path=tmp_path / "longshot.db",
        guild_id=None, setting_defaults=SETTING_DEFAULTS | settings,
    )
    bot = Longshot(config)
    await bot.wire()

    espn, sleeper, channel = FakeEspn(week), FakeSleeper(), Recorder()
    odds = FakeOdds(espn, bot.linebook._record_usage)
    bot.espn, bot.sleeper = espn, sleeper
    bot.nfl = Nfl(espn, bot.db, bot.settings)
    bot.linebook._odds = odds

    world = World(bot, espn, odds, sleeper, channel)

    async def league_channel():
        return channel

    async def fetch_user(discord_id: int):
        return world.dms.setdefault(discord_id, Recorder())

    bot.league_channel = league_channel
    bot.get_user = lambda discord_id: None
    bot.fetch_user = fetch_user

    await bot.db.set_state("league_id", "123")
    await bot.db.set_state("commissioner_id", 99)
    for n in range(1, members + 1):
        await bot.db.link_member(n, n, f"Team {n}", f"u{n}")
    return world
