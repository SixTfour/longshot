from datetime import datetime, timezone

import pytest

from longshot.domain.schedule import (
    ET,
    DayTime,
    kickoff_text,
    parse_day_time,
    parse_reminders,
    week_anchor,
    week_times,
)

# Week 6 of 2026 opens with Seahawks at Broncos, Thursday 15 October, 8:15 PM Eastern.
THURSDAY_KICKOFF = datetime(2026, 10, 16, 0, 15, tzinfo=timezone.utc)


def et(*args):
    return datetime(*args, tzinfo=ET)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("thu 19:00", DayTime(3, 19, 0)),
        ("Thursday 7pm", DayTime(3, 19, 0)),
        ("thurs 7:30 PM", DayTime(3, 19, 30)),
        ("sun 12:30pm", DayTime(6, 12, 30)),
        ("sun 12am", DayTime(6, 0, 0)),
        ("tue 10:00", DayTime(1, 10, 0)),
        ("mon 9", DayTime(0, 9, 0)),
    ],
)
def test_parse_day_time(text, expected):
    assert parse_day_time(text) == expected


@pytest.mark.parametrize("text", ["", "19:00", "someday 10:00", "thu 25:00", "thu 13pm", "thu 10:75", "thu"])
def test_parse_day_time_rejects(text):
    with pytest.raises(ValueError):
        parse_day_time(text)


def test_day_time_round_trips_through_its_text():
    assert parse_day_time(str(DayTime(3, 19, 0))) == DayTime(3, 19, 0)
    assert DayTime(3, 19, 0).pretty == "Thursday 7:00 PM ET"


def test_parse_reminders():
    assert parse_reminders("2, 24h, 7") == [24.0, 7.0, 2.0]
    assert parse_reminders("") == []
    with pytest.raises(ValueError):
        parse_reminders("soon")
    with pytest.raises(ValueError):
        parse_reminders("0")


def test_anchor_is_the_tuesday_before_the_first_kickoff():
    assert week_anchor(THURSDAY_KICKOFF) == et(2026, 10, 13, 0, 0)
    # A week that opens on a Wednesday or on the Tuesday itself.
    assert week_anchor(et(2026, 12, 23, 13, 0)) == et(2026, 12, 22, 0, 0)
    assert week_anchor(et(2026, 10, 13, 20, 0)) == et(2026, 10, 13, 0, 0)


def test_default_week_times():
    times = week_times(THURSDAY_KICKOFF, parse_day_time("tue 10:00"), parse_day_time("thu 19:00"), [24, 7, 2])
    assert times.announce_at == et(2026, 10, 13, 10, 0)
    assert times.deadline_at == et(2026, 10, 15, 19, 0)
    assert times.reminders == {
        24: et(2026, 10, 14, 19, 0),
        7: et(2026, 10, 15, 12, 0),
        2: et(2026, 10, 15, 17, 0),
    }
    # The Thursday game kicks off after the default deadline, so it can be picked.
    assert THURSDAY_KICKOFF > times.deadline_at


def test_sunday_deadline_falls_after_the_thursday_game():
    times = week_times(THURSDAY_KICKOFF, parse_day_time("tue 10:00"), parse_day_time("sun 12:00"), [2])
    assert times.deadline_at == et(2026, 10, 18, 12, 0)
    assert THURSDAY_KICKOFF < times.deadline_at


def test_monday_deadline_belongs_to_the_end_of_the_week():
    times = week_times(THURSDAY_KICKOFF, parse_day_time("tue 10:00"), parse_day_time("mon 18:00"), [])
    assert times.deadline_at == et(2026, 10, 19, 18, 0)


def test_times_hold_across_the_clock_change():
    # US clocks go back on Sunday 1 November 2026.
    kickoff = et(2026, 10, 29, 20, 15)
    times = week_times(kickoff, parse_day_time("tue 10:00"), parse_day_time("sun 12:00"), [24])
    assert times.deadline_at == et(2026, 11, 1, 12, 0)
    assert times.deadline_at.utcoffset().total_seconds() == -5 * 3600
    assert times.announce_at.utcoffset().total_seconds() == -4 * 3600


def test_kickoff_text_is_eastern():
    assert kickoff_text(THURSDAY_KICKOFF) == "Thu 8:15 PM"
