"""Slates, the Thanksgiving split and deadlines, checked against the real 2026 schedule (ESPN, saved 2026-10-11)."""

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from longshot.config import SETTING_DEFAULTS
from longshot.domain.cards import BONUS, MAIN, card_title, split_week
from longshot.domain.schedule import ET, parse_day_time, parse_reminders, week_times
from longshot.domain.slates import build_slates, holiday, thanksgiving

# The shipped defaults: announce Tuesday 10:00, main deadline Saturday 9 PM, bonus deadline Tuesday 9 PM.
ANNOUNCE = parse_day_time(SETTING_DEFAULTS["announce"])
DEADLINE = parse_day_time(SETTING_DEFAULTS["deadline"])
BONUS_DEADLINE = parse_day_time(SETTING_DEFAULTS["bonus_deadline"])
REMINDERS = parse_reminders(SETTING_DEFAULTS["reminders"])


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


# --- which parlays a week has ----------------------------------------------------


def first_kickoff(games):
    return min(game.kickoff for game in games if game.time_set)


def days(games):
    return sorted({f"{game.kickoff.astimezone(ET):%a}" for game in games})


def test_an_ordinary_week_has_one_parlay(week):
    cards = split_week(week(6))
    assert list(cards) == [MAIN]
    assert len(cards[MAIN]) == 14


def test_thanksgiving_week_has_a_bonus_parlay_then_the_main_one(week):
    cards = split_week(week(12))
    assert list(cards) == [BONUS, MAIN]  # in the order they are played
    assert days(cards[BONUS]) == ["Fri", "Thu", "Wed"]
    assert days(cards[MAIN]) == ["Mon", "Sun"]
    assert (len(cards[BONUS]), len(cards[MAIN])) == (5, 11)


def test_christmas_and_opening_weeks_are_treated_like_any_other(week):
    assert list(split_week(week(16))) == [MAIN]
    assert len(split_week(week(16))[MAIN]) == 16  # games with no kickoff time yet stay in the week
    assert list(split_week(week(1))) == [MAIN]


def test_each_thanksgiving_parlay_has_its_own_slates(week):
    cards = split_week(week(12))
    assert shape(build_slates(cards[BONUS])) == [
        ("Wednesday night", 1),
        ("Thanksgiving early", 1),
        ("Thanksgiving late", 1),
        ("Thanksgiving night", 1),
        ("Black Friday", 1),
    ]
    assert shape(build_slates(cards[MAIN])) == [
        ("Sunday early", 6),
        ("Sunday late", 3),
        ("Sunday night", 1),
        ("Monday night", 1),
    ]


def test_titles():
    assert card_title(12, MAIN) == "Week 12"
    assert card_title(12, BONUS) == "Thanksgiving bonus"


# --- deadlines ---------------------------------------------------------------------


def test_default_deadline_is_saturday_9pm_eastern(week):
    games = week(6)
    times = week_times(first_kickoff(games), ANNOUNCE, DEADLINE, REMINDERS)
    assert times.announce_at == et(2026, 10, 13, 10, 0)
    assert times.deadline_at == et(2026, 10, 17, 21, 0)
    assert times.reminders == {
        24: et(2026, 10, 16, 21, 0),
        7: et(2026, 10, 17, 14, 0),
        2: et(2026, 10, 17, 19, 0),
    }
    # Sunday's and Monday's games can be picked; Thursday night's cannot.
    open_to_picks = [game for game in games if game.kickoff >= times.deadline_at]
    assert days(open_to_picks) == ["Mon", "Sun"]
    assert len(open_to_picks) == 13


def test_thanksgiving_bonus_deadline_is_tuesday_9pm_eastern(week):
    games = week(12)
    cards = split_week(games)
    times = week_times(first_kickoff(games), ANNOUNCE, BONUS_DEADLINE, REMINDERS)
    assert times.announce_at == et(2026, 11, 24, 10, 0)
    assert times.deadline_at == et(2026, 11, 24, 21, 0)
    # Every Wednesday, Thursday and Friday game kicks off after it.
    assert all(game.kickoff >= times.deadline_at for game in cards[BONUS])
    # Picks open at 10:00 that morning, so the 24-hour reminder is dropped.
    assert times.reminders == {7: et(2026, 11, 24, 14, 0), 2: et(2026, 11, 24, 19, 0)}


def test_thanksgiving_week_main_parlay_keeps_the_saturday_deadline(week):
    games = week(12)
    times = week_times(first_kickoff(games), ANNOUNCE, DEADLINE, REMINDERS)
    assert times.deadline_at == et(2026, 11, 28, 21, 0)
    assert all(game.kickoff >= times.deadline_at for game in split_week(games)[MAIN])


def test_a_deadline_never_moves_for_an_early_game(week):
    # Week 1 opens on a Wednesday; a Thursday-evening deadline stays where it was set.
    times = week_times(first_kickoff(week(1)), ANNOUNCE, parse_day_time("thu 19:00"), [])
    assert times.deadline_at == et(2026, 9, 10, 19, 0)
    assert first_kickoff(week(1)) < times.deadline_at


def test_reminders_that_would_precede_the_announcement_are_dropped(week):
    times = week_times(first_kickoff(week(12)), ANNOUNCE, BONUS_DEADLINE, [48, 24, 2])
    assert list(times.reminders) == [2]
