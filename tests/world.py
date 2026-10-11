"""A stand-in for Discord, Sleeper, ESPN and the odds feed, so a whole week can be played out in a test.

The schedule is the real Week 6 of 2026 and the box score is a real one
(Buccaneers at Cowboys), re-homed onto that week's Steelers at Buccaneers game.
The odds are hand-written in the shape The Odds API documents.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from conftest import load

from longshot.bot import Longshot
from longshot.clients.espn import Athlete, Game, Scoreboard, Team, parse_boxscore, parse_game_roster
from longshot.clients.odds import OddsApiError
from longshot.config import SETTING_DEFAULTS, Config
from longshot.domain.schedule import ET
from longshot.nfl import Nfl

SEASON, WEEK = 2026, 6
TNF, BUCS_GAME = "SEA @ DEN", "PIT @ TB"
NAMES = {
    "SEA": "Seattle Seahawks", "DEN": "Denver Broncos", "PIT": "Pittsburgh Steelers", "TB": "Tampa Bay Buccaneers",
}
IRVING, DANIELS, BENCHED = "4596448", "4596472", "999001"


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


def _team(abbr: str) -> Team:
    return Team(abbr, abbr, NAMES.get(abbr, f"Team {abbr}"))


def week6_games() -> dict[str, Game]:
    games = {}
    for row in load("espn_kickoffs_2026.json")[str(WEEK)]:
        away, home = row["game"].replace(" VS ", " @ ").split(" @ ")
        games[row["game"].replace(" VS ", " @ ")] = Game(
            espn_id=row["espn_id"], season=SEASON, week=WEEK,
            kickoff=datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")),
            home=_team(home), away=_team(away), state="pre", completed=False, status="STATUS_SCHEDULED",
            home_score=0, away_score=0,
        )
    return games


class FakeEspn:
    def __init__(self):
        self.games = week6_games()
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

    async def scoreboard(self, season=None, week=None) -> Scoreboard:
        self.requests += 1
        games = list(self.games.values()) if week in (None, WEEK) else []
        return Scoreboard(SEASON, 2, WEEK if week is None else week, sorted(games, key=lambda g: g.kickoff))

    async def boxscore(self, event_id: str):
        game = next(g for g in self.games.values() if g.espn_id == event_id)
        return self.box if game.label == BUCS_GAME and game.state != "pre" else {}

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
        return [self._event(TNF), self._event(BUCS_GAME)]

    async def main_markets(self, bookmaker: str) -> list[dict]:
        await self._spend("main", 3)
        markets = [
            {"key": "h2h", "outcomes": [_outcome(NAMES["DEN"], -140), _outcome(NAMES["SEA"], 120)]},
            {"key": "spreads", "outcomes": [_outcome(NAMES["DEN"], -110, -2.5), _outcome(NAMES["SEA"], -110, 2.5)]},
            {"key": "totals", "outcomes": [_outcome("Over", -110, 43.5), _outcome("Under", -110, 43.5)]},
        ]
        return [self._event(TNF) | {"bookmakers": [{"key": bookmaker, "markets": markets}]}]

    async def event_markets(self, event_id: str, markets, bookmaker: str) -> dict:
        await self._spend(f"event:{','.join(markets)}", len(markets))
        label = TNF if event_id == self._event(TNF)["id"] else BUCS_GAME
        available = {
            "alternate_spreads": [_outcome(NAMES["DEN"], 150, -6.5), _outcome(NAMES["SEA"], -190, 6.5)],
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
        if label == TNF:
            found = [market for market in found if market["key"].startswith("alternate")]
        else:
            found = [market for market in found if market["key"].startswith("player")]
        return self._event(label) | {"bookmakers": [{"key": bookmaker, "markets": found}] if found else []}


class FakeSleeper:
    def __init__(self):
        self.scores = {1: 121.5, 2: 78.32, 3: 99.0}

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

    async def week(self) -> dict:
        return await self.bot.db.week(SEASON, WEEK)

    async def statuses(self) -> dict[int, str]:
        return {pick["discord_id"]: pick["status"] for pick in await self.bot.db.week_picks(SEASON, WEEK)}

    async def close(self) -> None:
        await self.bot.close()


async def build(tmp_path: Path, *, members: int = 3, **settings: str) -> World:
    """A bot connected to the league, with members 1..n linked to rosters 1..n."""
    config = Config(
        discord_token="not-a-real-token", odds_api_key="not-a-real-key", db_path=tmp_path / "longshot.db",
        guild_id=None, setting_defaults=SETTING_DEFAULTS | settings,
    )
    bot = Longshot(config)
    await bot.wire()

    espn, sleeper, channel = FakeEspn(), FakeSleeper(), Recorder()
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
