"""When each step of the weekly cycle happens.

An NFL week runs Tuesday to Monday. A week's cycle is anchored on the Tuesday
at or before its first kickoff, and every configured day and time is taken as
its first occurrence from that Tuesday. All settings are US Eastern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_TUESDAY = 1
_DAY_TIME = re.compile(r"^([a-z]+)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


@dataclass(frozen=True)
class DayTime:
    weekday: int  # Monday is 0
    hour: int
    minute: int

    def __str__(self) -> str:
        return f"{_DAYS[self.weekday]} {self.hour:02d}:{self.minute:02d}"

    @property
    def pretty(self) -> str:
        clock = time(self.hour, self.minute).strftime("%I:%M %p").lstrip("0")
        return f"{_DAY_NAMES[self.weekday]} {clock} ET"

    @property
    def offset(self) -> timedelta:
        """How far into the Tuesday-to-Monday week this falls."""
        days = (self.weekday - _TUESDAY) % 7
        return timedelta(days=days, hours=self.hour, minutes=self.minute)


def parse_day_time(text: str) -> DayTime:
    """Read 'thu 19:00', 'Thursday 7pm' or 'sun 12:30 pm'."""
    match = _DAY_TIME.match(text.strip().lower())
    if not match:
        raise ValueError("Use a day and a time, such as `thu 19:00` or `sunday 12:30pm`.")
    day, hour, minute, meridiem = match.groups()
    weekday = next((index for index, name in enumerate(_DAYS) if day.startswith(name)), None)
    if weekday is None or not _DAY_NAMES[weekday].lower().startswith(day):
        raise ValueError(f"'{day}' is not a day of the week.")
    hour, minute = int(hour), int(minute or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            raise ValueError("With am or pm, the hour must be between 1 and 12.")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ValueError("That is not a valid time of day.")
    return DayTime(weekday, hour, minute)


def parse_reminders(text: str) -> list[float]:
    """Read '24, 7, 2' as hours before the deadline, longest first. Empty means no reminders."""
    hours: list[float] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().lower().removesuffix("h")
        if not part:
            continue
        try:
            value = float(part)
        except ValueError:
            raise ValueError("Use hours before the deadline, separated by commas, such as `24, 7, 2`.") from None
        if not 0 < value <= 144:
            raise ValueError("Each reminder must be between 0 and 144 hours before the deadline.")
        hours.append(value)
    return sorted(set(hours), reverse=True)


def week_anchor(first_kickoff: datetime) -> datetime:
    """Midnight Eastern on the Tuesday at or before a week's first kickoff."""
    local = first_kickoff.astimezone(ET)
    days_back = (local.weekday() - _TUESDAY) % 7
    return datetime.combine(local.date() - timedelta(days=days_back), time(0, 0), tzinfo=ET)


def occurrence(anchor: datetime, when: DayTime) -> datetime:
    """The first time the given day and time comes round, from the anchor Tuesday."""
    day = anchor.date() + timedelta(days=when.offset.days)
    return datetime.combine(day, time(when.hour, when.minute), tzinfo=ET)


@dataclass(frozen=True)
class WeekTimes:
    announce_at: datetime
    deadline_at: datetime
    reminders: dict[float, datetime]  # hours before the deadline -> when to send


def week_times(first_kickoff: datetime, announce: DayTime, deadline: DayTime, reminders: list[float]) -> WeekTimes:
    anchor = week_anchor(first_kickoff)
    deadline_at = occurrence(anchor, deadline)
    return WeekTimes(
        announce_at=occurrence(anchor, announce),
        deadline_at=deadline_at,
        reminders={hours: deadline_at - timedelta(hours=hours) for hours in reminders},
    )


def kickoff_text(kickoff: datetime) -> str:
    """'Sun 1:00 PM' in Eastern, for places Discord cannot render a timestamp."""
    local = kickoff.astimezone(ET)
    return f"{local:%a} {local.strftime('%I:%M %p').lstrip('0')}"


def stamp(moment: datetime, style: str = "f") -> str:
    """A Discord timestamp, shown to each reader in their own timezone."""
    return f"<t:{int(moment.timestamp())}:{style}>"
