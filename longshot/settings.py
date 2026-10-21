"""The settings a commissioner can change from Discord with /config.

Values live in the database. Until one is changed, its starting value comes
from the DEFAULT_* environment variables (see config.py).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .db import Database
from .domain.cards import BONUS, MAIN
from .domain.schedule import DayTime, parse_day_time, parse_reminders

# Bookmaker keys The Odds API uses for US sportsbooks. Offered as suggestions;
# any well-formed key is accepted, because the feed's list changes over time.
KNOWN_SPORTSBOOKS = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "williamhill_us": "Caesars",
    "espnbet": "ESPN BET",
    "fanatics": "Fanatics",
    "betrivers": "BetRivers",
    "hardrockbet": "Hard Rock Bet",
    "bovada": "Bovada",
    "betonlineag": "BetOnline.ag",
}


def _day_time(text: str) -> str:
    return str(parse_day_time(text))


def _reminders(text: str) -> str:
    return ",".join(f"{hours:g}" for hours in parse_reminders(text))


def _sportsbook(text: str) -> str:
    key = text.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,40}", key):
        raise ValueError("Use the sportsbook's key from The Odds API, such as `draftkings` or `fanduel`.")
    return key


def _positive(low: float, high: float, what: str) -> Callable[[str], str]:
    def clean(text: str) -> str:
        try:
            value = float(text.strip().lstrip("$"))
        except ValueError:
            raise ValueError(f"{what} must be a number.") from None
        if not low <= value <= high:
            raise ValueError(f"{what} must be between {low:g} and {high:g}.")
        return f"{value:g}"

    return clean


@dataclass(frozen=True)
class Spec:
    label: str
    hint: str
    clean: Callable[[str], str]  # validates, and returns the text that is stored


SPECS: dict[str, Spec] = {
    "deadline": Spec("Pick deadline", "Day and time, US Eastern: `sat 21:00`, `sunday 12:30pm`", _day_time),
    "bonus_deadline": Spec(
        "Thanksgiving bonus deadline", "Day and time, US Eastern, for the Wednesday-to-Friday parlay: `tue 21:00`",
        _day_time,
    ),
    "sportsbook": Spec("Sportsbook", "`draftkings`, `fanduel`, `betmgm`, ...", _sportsbook),
    "announce": Spec("Loser announcement", "Day and time, US Eastern: `tue 10:00`", _day_time),
    "reminders": Spec("Reminders", "Hours before the deadline: `24, 7, 2`. Empty for none.", _reminders),
    "stake": Spec("Stake", "Dollars the loser owes: `10`", _positive(0.01, 100000, "The stake")),
    "cache_hours": Spec("Line cache", "Hours to reuse fetched lines: `12`", _positive(0.25, 72, "Cache hours")),
    "injury_hold": Spec(
        "Injury hold",
        "Hours a miss waits for your `/injury` ruling when ESPN shows the player was hurt but came back: `2`",
        _positive(0, 48, "The injury hold"),
    ),
}


DEADLINES = ("deadline", "bonus_deadline")


class Settings:
    def __init__(self, db: Database, defaults: dict[str, str]):
        self._db = db
        self._defaults = defaults

    def check_defaults(self) -> None:
        """Fail at startup, with the variable's name, if a DEFAULT_* value is malformed."""
        for key, spec in SPECS.items():
            try:
                spec.clean(self._defaults[key])
            except ValueError as error:
                raise ValueError(f"DEFAULT_{key.upper()} is invalid. {error}") from None
        announce = parse_day_time(self._defaults["announce"])
        for key in DEADLINES:
            self._check_order(announce, parse_day_time(self._defaults[key]), SPECS[key].label)

    @staticmethod
    def _check_order(announce: DayTime, deadline: DayTime, label: str) -> None:
        if deadline.offset <= announce.offset:
            raise ValueError(
                f"The {label.lower()} ({deadline.pretty}) must come after the loser announcement "
                f"({announce.pretty}) in the Tuesday-to-Monday week."
            )

    async def raw(self, key: str) -> str:
        stored = await self._db.get_setting(key)
        return stored if stored is not None else SPECS[key].clean(self._defaults[key])

    async def set(self, key: str, text: str) -> str:
        value = SPECS[key].clean(text)
        if key == "announce" or key in DEADLINES:
            announce = parse_day_time(value if key == "announce" else await self.raw("announce"))
            for name in DEADLINES:
                deadline = parse_day_time(value if key == name else await self.raw(name))
                self._check_order(announce, deadline, SPECS[name].label)
        await self._db.set_setting(key, value)
        return value

    async def deadline(self, card: str = MAIN) -> DayTime:
        return parse_day_time(await self.raw("bonus_deadline" if card == BONUS else "deadline"))

    async def announce(self) -> DayTime:
        return parse_day_time(await self.raw("announce"))

    async def reminders(self) -> list[float]:
        return parse_reminders(await self.raw("reminders"))

    async def sportsbook(self) -> str:
        return await self.raw("sportsbook")

    async def sportsbook_name(self) -> str:
        key = await self.sportsbook()
        return KNOWN_SPORTSBOOKS.get(key, key)

    async def stake(self) -> float:
        return float(await self.raw("stake"))

    async def cache_hours(self) -> float:
        return float(await self.raw("cache_hours"))

    async def injury_hold(self) -> float:
        return float(await self.raw("injury_hold"))

    async def describe(self, key: str) -> str:
        """The current value, written for a person."""
        value = await self.raw(key)
        if key == "announce" or key in DEADLINES:
            return parse_day_time(value).pretty
        if key == "reminders":
            hours = parse_reminders(value)
            return ", ".join(f"{h:g}h" for h in hours) + " before the deadline" if hours else "None"
        if key == "sportsbook":
            return f"{await self.sportsbook_name()} (`{value}`)"
        if key == "stake":
            return f"${float(value):,.2f}".removesuffix(".00")
        return f"{value} hours"
