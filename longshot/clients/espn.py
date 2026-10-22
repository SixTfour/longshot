"""ESPN's public JSON feeds: schedule, live scores, box scores and rosters.

These are unofficial and undocumented. Everything read from them goes through
the parse_* functions below, so a change on ESPN's side breaks in one place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import aiohttp

from ..domain.injuries import InjuryNote, parse_notes
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
    week: int
    games: list[Game]


@dataclass(frozen=True)
class SeasonCalendar:
    """When the regular season runs, week by week, from ESPN's own calendar."""

    season: int  # the year the season starts in
    weeks: tuple[tuple[int, datetime, datetime], ...]  # (week number, start, end), in order

    @property
    def last_week(self) -> int:
        return self.weeks[-1][0] if self.weeks else 0

    def regular_week(self, now: datetime) -> int | None:
        """The regular-season week that contains now, or None in the preseason, playoffs and offseason."""
        if not self.weeks or not self.weeks[0][1] <= now <= self.weeks[-1][2]:
            return None
        # ESPN leaves a minute between one week's end and the next one's start; go by the starts.
        return max(week for week, start, _ in self.weeks if start <= now)


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


@dataclass(frozen=True)
class GameReport:
    """What one game summary says: every player's stat line, and the injury notes from the play-by-play."""

    players: dict[str, PlayerLine]
    injuries: list[InjuryNote]


def _number(text: Any) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _season_year(payload: dict[str, Any]) -> int:
    """The season's year. ESPN gives it at the top of some responses and only under the league in others."""
    league = (payload.get("leagues") or [{}])[0]
    year = (payload.get("season") or {}).get("year") or (league.get("season") or {}).get("year")
    if year is None:
        raise EspnError("ESPN's response does not say which season it is.")
    return int(year)


def parse_calendar(payload: dict[str, Any]) -> SeasonCalendar:
    """The regular season's weeks. Empty if ESPN lists none, which reads as 'not in season'."""
    league = (payload.get("leagues") or [{}])[0]
    weeks: list[tuple[int, datetime, datetime]] = []
    for span in league.get("calendar") or []:
        if not isinstance(span, dict) or str(span.get("value")) != str(REGULAR_SEASON):
            continue
        for entry in span.get("entries") or []:
            weeks.append((int(entry["value"]), _when(entry["startDate"]), _when(entry["endDate"])))
    return SeasonCalendar(_season_year(payload), tuple(sorted(weeks)))


def parse_scoreboard(payload: dict[str, Any]) -> Scoreboard:
    season = _season_year(payload)
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
                kickoff=_when(event["date"]),
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
    return Scoreboard(season, week, sorted(games, key=lambda game: game.kickoff))


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


def parse_injuries(payload: dict[str, Any]) -> list[InjuryNote]:
    """The play-by-play's injury notes, in the order they happened.

    The summary's own 'injuries' section is not used: it is the teams' current
    injury report, not a record of the game.
    """
    drives = payload.get("drives") or {}
    played = list(drives.get("previous") or [])
    if isinstance(drives.get("current"), dict):
        played.append(drives["current"])
    return parse_notes(
        ((play.get("period") or {}).get("number"), play.get("text") or "")
        for drive in played
        for play in drive.get("plays") or []
    )


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

    async def scoreboard(self, season: int, week: int) -> Scoreboard:
        """One regular-season week's games."""
        payload = await self._get(f"{SITE}/scoreboard", dates=season, seasontype=REGULAR_SEASON, week=week)
        return parse_scoreboard(payload)

    async def calendar(self) -> SeasonCalendar:
        """The current season's calendar."""
        return parse_calendar(await self._get(f"{SITE}/scoreboard"))

    async def report(self, event_id: str) -> GameReport:
        payload = await self._get(f"{SITE}/summary", event=event_id)
        return GameReport(parse_boxscore(payload), parse_injuries(payload))

    async def boxscore(self, event_id: str) -> dict[str, PlayerLine]:
        return (await self.report(event_id)).players

    async def team_roster(self, team_id: str) -> list[Athlete]:
        return parse_team_roster(await self._get(f"{SITE}/teams/{team_id}/roster"))

    async def played(self, game: Game) -> dict[str, bool]:
        """Athlete id -> whether they played, for both teams. Used when a player has no stat line."""
        result: dict[str, bool] = {}
        for team in (game.home, game.away):
            url = f"{CORE}/events/{game.espn_id}/competitions/{game.espn_id}/competitors/{team.id}/roster"
            result.update(parse_game_roster(await self._get(url)))
        return result
