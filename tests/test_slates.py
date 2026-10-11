"""Slates and holiday-week deadlines, checked against the real 2026 schedule (ESPN, saved 2026-10-11)."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from longshot.domain.schedule import ET, parse_day_time, week_times
from longshot.domain.slates import build_slates, holiday, thanksgiving

TUE_10 = parse_day_time("tue 10:00")
THU_19 = parse_day_time("thu 19:00")


@dataclass(frozen=True)
class FakeGame:
    espn_id: str
    kickoff: datetime
    time_set: bool = True


@pytest.fixture
def week(fixture):
    schedule = fixture("espn_kickoffs_2026.json")

    def games(number: int) -> list[FakeGame]:
        return [
            FakeGame(row["espn_id"], datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")), row["time_set"])
            for row in schedule[str(number)]
        ]

    return games


def shape(slates):
    return [(slate.name, len(slate.game_ids)) for slate in slates]


def et(*args):
    return datetime(*args, tzinfo=ET)


def test_holiday_dates():
    assert thanksgiving(2026) == date(2026, 11, 26)
    assert thanksgiving(2027) == date(2027, 11, 25)
    assert holiday(date(2026, 11, 26)) == "Thanksgiving"
    assert holiday(date(2026, 11, 27)) == "Black Friday"
    assert holiday(date(2026, 12, 25)) == "Christmas Day"
    assert holiday(date(2027, 1, 1)) == "New Year's Day"
    assert holiday(date(2026, 10, 18)) is None


def test_ordinary_week_with_a_london_game(week):
    assert shape(build_slates(week(6))) == [
        ("Thursday night", 1),
        ("Sunday morning", 1),
        ("Sunday early", 7),
        ("Sunday late", 3),  # the 4:05 and 4:25 kickoffs are one slate
        ("Sunday night", 1),
        ("Monday night", 1),
    ]


def test_thanksgiving_week(week):
    assert shape(build_slates(week(12))) == [
        ("Wednesday night", 1),
        ("Thanksgiving early", 1),
        ("Thanksgiving late", 1),
        ("Thanksgiving night", 1),
        ("Black Friday", 1),
        ("Sunday early", 6),
        ("Sunday late", 3),
        ("Sunday night", 1),
        ("Monday night", 1),
    ]


def test_christmas_week_leaves_out_games_with_no_kickoff_time_yet(week):
    slates = build_slates(week(16))
    assert shape(slates) == [
        ("Christmas Eve", 1),
        ("Christmas Day early", 1),
        ("Christmas Day late", 1),
        ("Christmas Day night", 1),
        ("Sunday early", 4),
        ("Sunday late", 2),
        ("Sunday night", 1),
        ("Monday night", 1),
    ]
    assert sum(len(slate.game_ids) for slate in slates) == 12  # 4 of the 16 games are still TBD


def test_opening_week_starts_on_a_wednesday(week):
    assert shape(build_slates(week(1)))[:2] == [("Wednesday night", 1), ("Thursday night", 1)]


def test_every_game_is_in_exactly_one_slate(week):
    for number in (1, 6, 12):
        games = week(number)
        placed = [game_id for slate in build_slates(games) for game_id in slate.game_ids]
        assert sorted(placed) == sorted(game.espn_id for game in games)


def test_slate_keys_are_stable_and_ordered(week):
    slates = build_slates(week(6))
    assert [slate.key for slate in slates] == sorted(slate.key for slate in slates)
    assert slates[0].key == "2026-10-16T00:15+00:00"
    assert build_slates(reversed(week(6))) == slates


# --- deadlines in weeks that start early ---------------------------------------


def first_kickoff(games):
    return min(game.kickoff for game in games if game.time_set)


def test_ordinary_week_keeps_the_configured_deadline(week):
    times = week_times(first_kickoff(week(6)), TUE_10, THU_19, [24, 7, 2])
    assert times.deadline_at == et(2026, 10, 15, 19, 0)
    assert times.moved_early is False


def test_thanksgiving_week_deadline_moves_before_the_wednesday_game(week):
    times = week_times(first_kickoff(week(12)), TUE_10, THU_19, [24, 7, 2])
    # The week opens Wednesday 8:00 PM, before the usual Thursday 7:00 PM deadline.
    assert times.moved_early is True
    assert times.deadline_at == et(2026, 11, 25, 19, 0)
    assert times.announce_at == et(2026, 11, 24, 10, 0)
    assert times.reminders == {
        24: et(2026, 11, 24, 19, 0),
        7: et(2026, 11, 25, 12, 0),
        2: et(2026, 11, 25, 17, 0),
    }
    # Every game that week, Thanksgiving's three included, kicks off after the deadline.
    assert all(game.kickoff >= times.deadline_at for game in week(12))


def test_christmas_week_needs_no_change_when_it_opens_thursday_night(week):
    times = week_times(first_kickoff(week(16)), TUE_10, THU_19, [24])
    assert times.moved_early is False
    assert times.deadline_at == et(2026, 12, 24, 19, 0)


def test_opening_week_deadline_moves_before_the_wednesday_opener(week):
    times = week_times(first_kickoff(week(1)), TUE_10, THU_19, [24, 7, 2])
    assert times.moved_early is True
    assert times.deadline_at == et(2026, 9, 9, 19, 20)


def test_a_weekend_deadline_is_never_moved(week):
    # A league that only picks Sunday games has chosen to leave out everything before it.
    times = week_times(first_kickoff(week(12)), TUE_10, parse_day_time("sun 12:00"), [2])
    assert times.moved_early is False
    assert times.deadline_at == et(2026, 11, 29, 12, 0)


def test_reminders_that_would_precede_the_announcement_are_dropped(week):
    times = week_times(first_kickoff(week(12)), TUE_10, THU_19, [48, 24])
    # 48 hours before Wednesday 7:00 PM is Monday, before picks open on Tuesday.
    assert list(times.reminders) == [24]
