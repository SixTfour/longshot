"""ESPN's public JSON feeds: schedule, live scores, box scores and rosters.

These are unofficial and undocumented. Everything read from them goes through
the parse_* functions below, so a change on ESPN's side breaks in one place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import aiohttp

from ..domain.markets import PICKABLE_POSITIONS

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Longshot)"}

REGULAR_SEASON = 2
# Roster sections whose players cannot have a line this week.
_UNAVAILABLE_GROUPS = {"injuredReserveOrOut", "suspended"}


class EspnError(RuntimeError):
    pass


@dataclass(frozen=True)
class Team:
    id: str
    abbr: str
    name: str


@dataclass(frozen=True)
class Game:
    espn_id: str
    season: int
    week: int
    kickoff: datetime
    home: Team
    away: Team
    state: str  # 'pre', 'in' or 'post'
    completed: bool
    status: str  # ESPN's name, e.g. STATUS_FINAL, STATUS_POSTPONED
    home_score: int
    away_score: int
    time_set: bool = True  # False while the NFL has not fixed the kickoff (late-season flex games)

    @property
    def label(self) -> str:
        return f"{self.away.abbr} @ {self.home.abbr}"

    @property
    def final(self) -> bool:
        return self.completed and self.state == "post" and not self.called_off

    @property
    def over(self) -> bool:
        """Nothing more will happen in this game this week."""
        return self.final or self.called_off

    @property
    def called_off(self) -> bool:
        """Postponed, cancelled or otherwise over without a final score."""
        return self.status in {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_FORFEIT", "STATUS_SUSPENDED"}

    def team(self, abbr: str) -> Team | None:
        return next((team for team in (self.home, self.away) if team.abbr == abbr), None)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["kickoff"] = self.kickoff.isoformat()
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Game:
        fields = dict(data)
        fields["kickoff"] = datetime.fromisoformat(fields["kickoff"])
        fields["home"] = Team(**fields["home"])
        fields["away"] = Team(**fields["away"])
        return cls(**fields)


@dataclass(frozen=True)
class Scoreboard:
    season: int
    season_type: int
    week: int
    games: list[Game]


@dataclass(frozen=True)
class Athlete:
    id: str
    name: str
    position: str
    team: str  # abbreviation


@dataclass(frozen=True)
class PlayerLine:
    name: str
    team: str
    stats: dict[str, float]


def _number(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_scoreboard(payload: dict[str, Any]) -> Scoreboard:
    season = int(payload["season"]["year"])
    season_type = int(payload["season"]["type"])
    week = int((payload.get("week") or {}).get("number") or 0)
    games = []
    for event in payload.get("events", []):
        competition = event["competitions"][0]
        sides = {}
        scores = {}
        for competitor in competition["competitors"]:
            team = competitor["team"]
            sides[competitor["homeAway"]] = Team(str(team["id"]), team["abbreviation"], team["displayName"])
            scores[competitor["homeAway"]] = int(_number(competitor.get("score")) or 0)
        status = event["status"]["type"]
        games.append(
            Game(
                espn_id=str(event["id"]),
                season=season,
                week=week,
                kickoff=datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
                home=sides["home"],
                away=sides["away"],
                state=status.get("state", "pre"),
                completed=bool(status.get("completed")),
                status=status.get("name", ""),
                home_score=scores["home"],
                away_score=scores["away"],
                time_set=bool(competition.get("timeValid", True)),
            )
        )
    return Scoreboard(season, season_type, week, sorted(games, key=lambda game: game.kickoff))


def parse_boxscore(payload: dict[str, Any]) -> dict[str, PlayerLine]:
    """Every player with a stat line, keyed by ESPN athlete id.

    Stats are flattened to '<category>.<key>'. Paired columns such as
    'completions/passingAttempts' = '19/25' become two entries.
    """
    players: dict[str, PlayerLine] = {}
    for team in (payload.get("boxscore") or {}).get("players", []):
        abbr = team["team"]["abbreviation"]
        for category in team.get("statistics", []):
            name, keys = category["name"], category.get("keys", [])
            for entry in category.get("athletes", []):
                athlete = entry["athlete"]
                line = players.setdefault(
                    str(athlete["id"]), PlayerLine(athlete.get("displayName", ""), abbr, {})
                )
                for key, raw in zip(keys, entry.get("stats", []), strict=False):
                    for separator in ("/", "-"):
                        if separator in key:
                            parts = zip(key.split(separator), str(raw).split(separator), strict=False)
                            break
                    else:
                        parts = [(key, raw)]
                    for part_key, part_value in parts:
                        value = _number(part_value)
                        if value is not None:
                            line.stats[f"{name}.{part_key}"] = value
    return players


def parse_team_roster(payload: dict[str, Any]) -> list[Athlete]:
    """Players who could have a prop line: skill positions and kickers, not on IR or ruled out."""
    abbr = payload["team"]["abbreviation"]
    athletes = []
    for group in payload.get("athletes", []):
        if group.get("position") in _UNAVAILABLE_GROUPS:
            continue
        for item in group.get("items", []):
            position = (item.get("position") or {}).get("abbreviation", "")
            if position in PICKABLE_POSITIONS:
                athletes.append(Athlete(str(item["id"]), item.get("fullName") or item["displayName"], position, abbr))
    return athletes


def parse_game_roster(payload: dict[str, Any]) -> dict[str, bool]:
    """Athlete id -> whether they played, for one team in one game."""
    return {str(entry["playerId"]): not entry.get("didNotPlay", False) for entry in payload.get("entries", [])}


class EspnClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def _get(self, url: str, **params: Any) -> dict[str, Any]:
        try:
            async with self._session.get(url, params=params, headers=HEADERS) as response:
                if response.status != 200:
                    raise EspnError(f"ESPN returned HTTP {response.status} for {url}")
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise EspnError(f"Could not reach ESPN: {error or type(error).__name__}") from error

    async def scoreboard(self, season: int | None = None, week: int | None = None) -> Scoreboard:
        """One regular-season week, or the current week when called with no arguments."""
        params: dict[str, Any] = {}
        if season is not None and week is not None:
            params = {"dates": season, "seasontype": REGULAR_SEASON, "week": week}
        return parse_scoreboard(await self._get(f"{SITE}/scoreboard", **params))

    async def boxscore(self, event_id: str) -> dict[str, PlayerLine]:
        return parse_boxscore(await self._get(f"{SITE}/summary", event=event_id))

    async def team_roster(self, team_id: str) -> list[Athlete]:
        return parse_team_roster(await self._get(f"{SITE}/teams/{team_id}/roster"))

    async def played(self, game: Game) -> dict[str, bool]:
        """Athlete id -> whether they played, for both teams. Used when a player has no stat line."""
        result: dict[str, bool] = {}
        for team in (game.home, game.away):
            url = f"{CORE}/events/{game.espn_id}/competitions/{game.espn_id}/competitors/{team.id}/roster"
            result.update(parse_game_roster(await self._get(url)))
        return result
