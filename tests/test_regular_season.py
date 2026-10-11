"""Nothing is posted outside the regular season, and a Sleeper league from another season is never trusted.

The season's dates are ESPN's real 2026 calendar (saved 2026-10-11).
"""

from datetime import datetime, timezone

import pytest
from test_week_flow import DEN_SPREAD, IRVING_150, THU_DEADLINE, TUE_ANNOUNCE, TUE_NOON, play
from world import BUCS_GAME, SEASON, TNF, WEEK, et

from longshot import weekly
from longshot.clients.espn import parse_calendar
from longshot.domain.loser import league_season
from longshot.domain.odds_math import parlay_decimal, total_return


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- the calendar ----------------------------------------------------------------


@pytest.fixture
def calendar(fixture):
    return parse_calendar(fixture("espn_calendar_2026.json"))


def test_calendar_has_the_regular_season_weeks(calendar):
    assert calendar.season == 2026
    assert [week for week, _, _ in calendar.weeks] == list(range(1, 19))
    assert calendar.last_week == 18
    assert calendar.weeks[0][1] == utc(2026, 9, 6, 7, 0)
    assert calendar.weeks[-1][2] == utc(2027, 1, 13, 7, 59)


@pytest.mark.parametrize(
    "moment, week",
    [
        (utc(2026, 6, 1, 12, 0), None),  # offseason before it
        (utc(2026, 8, 15, 17, 0), None),  # preseason
        (utc(2026, 9, 6, 6, 59), None),  # a minute before the season opens
        (utc(2026, 9, 6, 7, 0), 1),
        (utc(2026, 9, 9, 23, 0), 1),
        (utc(2026, 10, 13, 14, 1), 5),  # Tuesday: ESPN is still on Week 5 when Week 6's parlay opens
        (utc(2026, 10, 14, 6, 59, 30), 5),  # inside the minute ESPN leaves between two weeks
        (utc(2026, 10, 14, 7, 0), 6),
        (utc(2026, 11, 26, 18, 0), 12),  # Thanksgiving
        (utc(2027, 1, 10, 21, 0), 18),  # the last Sunday
        (utc(2027, 1, 13, 7, 59), 18),
        (utc(2027, 1, 13, 8, 0), None),  # the playoffs begin
        (utc(2027, 2, 14, 23, 30), None),  # Super Bowl Sunday
        (utc(2027, 5, 1, 12, 0), None),  # offseason after it
    ],
)
def test_regular_week(calendar, moment, week):
    assert calendar.regular_week(moment) == week


def test_a_response_with_no_calendar_reads_as_out_of_season():
    # What ESPN returns for a date it has no schedule for.
    empty = parse_calendar({"leagues": [{"season": {"year": 2026}}], "events": []})
    assert (empty.season, empty.weeks, empty.last_week) == (2026, (), 0)
    assert empty.regular_week(utc(2026, 10, 14, 7, 0)) is None


# --- the bot outside the regular season ----------------------------------------------


@pytest.mark.parametrize(
    "moment",
    [et(2026, 8, 20, 12, 0), et(2027, 1, 20, 12, 0), et(2027, 2, 14, 19, 0), et(2027, 4, 1, 10, 1)],
    ids=["preseason", "playoffs", "super-bowl", "offseason"],
)
def test_nothing_is_announced_outside_the_regular_season(tmp_path, monkeypatch, moment):
    async def scenario(world, at):
        assert await world.bot.nfl.regular_week(moment) is None
        assert await world.bot.nfl.active(moment) is None
        await at(moment)
        assert world.channel.sent == [] and world.dms == {}
        assert await world.bot.db.parlays() == []
        assert "not open" in await world.pick(1, *IRVING_150)  # commands still answer

    play(tmp_path, monkeypatch, scenario)


def test_an_unfinished_parlay_is_settled_silently_in_the_offseason(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)

        # The bot goes down before any game is played and comes back in February.
        channel.clear()
        world.espn.set(TNF, state="post", home=24, away=20)
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        world.espn.finish_everything()
        await at(et(2027, 2, 1, 12, 0))

        # Both legs won, and the win is on record, but nothing is posted months late.
        assert channel.sent == [] and world.dms == {}
        assert await world.statuses() == {1: "won", 2: "won"}
        week = await world.week()
        assert week["result"] == "won" and week["recap_at"]
        assert week["payout"] == total_return(10, parlay_decimal([320, -110]))

    play(tmp_path, monkeypatch, scenario)


def test_picks_left_open_are_closed_silently_in_the_offseason(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        world.channel.clear()

        world.espn.finish_everything()
        await at(et(2027, 3, 1, 12, 0))  # the deadline passed while the bot was off
        assert world.channel.sent == []
        week = await world.week()
        assert week["closed_at"] and week["recap_at"]
        assert sorted(row["discord_id"] for row in await world.bot.db.no_shows(SEASON)) == [2, 3]

    play(tmp_path, monkeypatch, scenario)


# --- a Sleeper league from another season ------------------------------------------------


def test_league_season():
    assert league_season({"season": "2026"}) == 2026
    assert league_season({"season": 2025}) == 2025
    assert league_season({}) is None
    assert league_season({"season": "soon"}) is None


def test_scores_from_last_seasons_league_are_not_used(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel, db = world.channel, world.bot.db
        world.sleeper.season = "2025"  # /setup was never redone for the new season

        await at(TUE_ANNOUNCE)
        text = channel.last()
        assert "the connected Sleeper league is from the 2025 season, not 2026" in text
        assert "run `/setup` with this season's league ID" in text
        assert "Team 2" not in text and "owes" not in text  # nobody is named from last year's scores
        assert "Picks are open" in text  # the week still goes ahead
        assert await db.debt(SEASON, WEEK) is None
        assert (await world.week())["loser_roster_id"] is None

        # The commissioner is told privately, once.
        assert len(world.dms[99].sent) == 1
        assert "**2025** Sleeper league" in world.dms[99].last() and "/setup" in world.dms[99].last()

        # After /setup with this season's league, re-running the announcement names the lowest scorer.
        world.sleeper.season = "2026"
        plan = await world.bot.nfl.active(TUE_NOON)
        assert await weekly.announce(world.bot, plan, TUE_NOON, force=True)
        assert "**Team 2** (<@2>)" in channel.last() and "owes **$10**" in channel.last()
        assert (await db.debt(SEASON, WEEK))["roster_id"] == 2
        assert len(world.dms[99].sent) == 1

    play(tmp_path, monkeypatch, scenario)


def test_a_league_that_does_not_state_its_season_is_used_as_before(tmp_path, monkeypatch):
    async def scenario(world, at):
        world.sleeper.season = None
        await at(TUE_ANNOUNCE)
        assert "**Team 2** (<@2>)" in world.channel.last()
        assert world.dms == {}

    play(tmp_path, monkeypatch, scenario)
