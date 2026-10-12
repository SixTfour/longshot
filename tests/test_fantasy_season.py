"""The bot runs only for the weeks the Sleeper league says its fantasy season covers."""

import pytest
from test_week_flow import DEN_SPREAD, IRVING_150, THU_DEADLINE, TUE_ANNOUNCE, TUE_NOON, play
from world import BUCS_GAME, SEASON, TNF, WEEK, et

from longshot.domain.fantasy import fantasy_season

BRACKET_3_ROUNDS = [{"r": 1}, {"r": 1}, {"r": 2}, {"r": 2}, {"r": 3}]


def league(status="in_season", **settings):
    base = {"start_week": 1, "playoff_week_start": 15, "playoff_teams": 6, "playoff_round_type": 0}
    return {"season": "2026", "status": status, "settings": base | settings}


# --- reading the season from Sleeper -----------------------------------------------


def test_a_real_league(fixture):
    """Sleeper's own example league from 2018: started in Week 2, playoffs from Week 14, three rounds."""
    saved = fixture("sleeper_league_2018.json")
    season = fantasy_season(saved["league"], saved["winners_bracket"])
    assert (season.season, season.status, season.start_week, season.last_week) == (2018, "complete", 2, 16)
    # The last week worked out from the bracket is the last week Sleeper itself recorded scoring.
    still_running = dict(saved["league"], status="in_season")
    assert fantasy_season(still_running, saved["winners_bracket"]).last_week == 16
    assert saved["league"]["settings"]["last_scored_leg"] == 16
    assert season.span == "Weeks 2 to 16"


def test_a_typical_league_ends_in_week_17():
    season = fantasy_season(league(), BRACKET_3_ROUNDS)
    assert (season.start_week, season.last_week) == (1, 17)
    assert [week for week in range(1, 19) if season.runs(week)] == list(range(1, 18))
    assert not season.runs(18)  # the NFL's last week is after the fantasy championship


@pytest.mark.parametrize("teams, last_week", [(2, 15), (4, 16), (6, 17), (8, 17)])
def test_rounds_come_from_the_number_of_playoff_teams_when_there_is_no_bracket_yet(teams, last_week):
    assert fantasy_season(league(playoff_teams=teams), None).last_week == last_week
    assert fantasy_season(league(playoff_teams=teams), []).last_week == last_week


def test_longer_playoff_rounds():
    # Sleeper's playoff_round_type: 1 is a two-week final, 2 is two weeks for every round.
    assert fantasy_season(league(playoff_week_start=14, playoff_round_type=1), BRACKET_3_ROUNDS).last_week == 17
    assert fantasy_season(league(playoff_week_start=12, playoff_round_type=2), BRACKET_3_ROUNDS).last_week == 17


def test_once_complete_the_last_scored_week_is_the_end():
    done = league("complete", last_scored_leg=16)
    assert fantasy_season(done, BRACKET_3_ROUNDS).last_week == 16


def test_a_league_that_has_not_drafted_is_not_running():
    for status in ("pre_draft", "drafting"):
        season = fantasy_season(league(status), BRACKET_3_ROUNDS)
        assert not season.started and not any(season.runs(week) for week in range(1, 19))


def test_a_league_with_no_playoff_week_runs_until_the_nfl_season_ends():
    season = fantasy_season({"season": "2026", "status": "in_season", "settings": {}})
    assert season.start_week == 1 and season.runs(18)
    assert season.span == "from Week 1"


def test_missing_or_odd_values_do_not_break_it():
    season = fantasy_season({"status": None, "settings": {"start_week": None, "playoff_week_start": "15"}})
    assert (season.season, season.status, season.start_week, season.last_week) == (None, "in_season", 1, 14)


# --- the bot ---------------------------------------------------------------------------


def nothing_runs(world):
    return world.channel.sent == [] and world.dms == {}


@pytest.mark.parametrize(
    "change",
    [
        {"settings": {"playoff_week_start": 4, "playoff_teams": 4}},  # playoffs were Weeks 4 and 5
        {"settings": {"start_week": 7}},  # the league starts next week
        {"status": "pre_draft"},
        {"status": "drafting"},
        {"status": "complete", "settings": {"last_scored_leg": 5}},
    ],
    ids=["season-over", "not-started", "pre-draft", "drafting", "complete"],
)
def test_no_parlay_runs_outside_the_fantasy_season(tmp_path, monkeypatch, change):
    async def scenario(world, at):
        world.sleeper.status = change.get("status", world.sleeper.status)
        world.sleeper.settings.update(change.get("settings", {}))
        world.sleeper.bracket = []
        # Week 6 of the NFL season, which is in the regular season but not in this league's.
        assert await world.bot.nfl.regular_week(TUE_NOON) is not None
        assert not (await world.bot.fantasy()).runs(WEEK)

        await at(TUE_ANNOUNCE)
        await at(THU_DEADLINE)
        assert nothing_runs(world)
        assert await world.bot.db.parlays() == []
        assert "not open" in await world.pick(1, *IRVING_150)

    play(tmp_path, monkeypatch, scenario)


def test_who_pays_in_a_typical_league():
    season = fantasy_season(league(), BRACKET_3_ROUNDS)
    paid_by_commissioner = [week for week in range(1, 18) if season.commissioner_pays(week)]
    assert paid_by_commissioner == [1, 15, 16, 17]
    assert season.commissioner_pays(1) == "it is the first week of the fantasy season"
    assert season.commissioner_pays(15) == "it is a fantasy playoff week"
    assert season.commissioner_pays(14) is None
    assert season.commissioner_weeks == "Week 1 and Weeks 15 to 17"


def test_who_pays_follows_the_leagues_own_playoff_weeks(fixture):
    saved = fixture("sleeper_league_2018.json")  # starts Week 2, playoffs Weeks 14 to 16
    season = fantasy_season(saved["league"], saved["winners_bracket"])
    assert [week for week in range(2, 17) if season.commissioner_pays(week)] == [2, 14, 15, 16]
    assert season.commissioner_weeks == "Week 2 and Weeks 14 to 16"
    # A league that does not say when its playoffs start has only the first week.
    open_ended = fantasy_season({"season": "2026", "status": "in_season", "settings": {}})
    assert [week for week in range(1, 19) if open_ended.commissioner_pays(week)] == [1]
    assert open_ended.commissioner_weeks == "Week 1"


def test_the_commissioner_pays_in_the_first_fantasy_week(tmp_path, monkeypatch):
    async def scenario(world, at):
        world.sleeper.settings["start_week"] = WEEK
        await at(TUE_ANNOUNCE)
        text = world.channel.last()
        assert (
            "The commissioner is paying for this week's parlay: it is the first week of the fantasy season." in text
        )
        assert "Picks are open" in text and "owes **" not in text and "Team 2" not in text
        assert world.channel.sent[-1].view is None  # no Mark paid button: nobody owes anything
        assert await world.bot.db.debt(SEASON, WEEK) is None
        assert (await world.week())["loser_roster_id"] is None

    play(tmp_path, monkeypatch, scenario)


def test_the_commissioner_pays_in_a_playoff_week(tmp_path, monkeypatch):
    async def scenario(world, at):
        world.sleeper.settings.update(playoff_week_start=WEEK)  # this league's playoffs are Weeks 6 to 8
        await at(TUE_ANNOUNCE)
        text = world.channel.last()
        assert "The commissioner is paying for this week's parlay: it is a fantasy playoff week." in text
        assert "Picks are open" in text and "owes **" not in text and "Team 2" not in text
        assert await world.bot.db.debt(SEASON, WEEK) is None

        # The parlay itself runs as usual.
        await at(TUE_NOON, tick=False)
        assert "Your Week 6 pick is" in await world.pick(1, *IRVING_150)
        await at(THU_DEADLINE)
        assert "Week 6 picks are closed" in world.channel.last()

    play(tmp_path, monkeypatch, scenario)


def test_the_lowest_scorer_pays_in_the_last_week_before_the_playoffs(tmp_path, monkeypatch):
    async def scenario(world, at):
        world.sleeper.settings.update(playoff_week_start=WEEK + 1)
        await at(TUE_ANNOUNCE)
        assert "**Team 2** had the lowest score" in world.channel.last() and "owes **$10**" in world.channel.last()
        assert (await world.bot.db.debt(SEASON, WEEK))["roster_id"] == 2

    play(tmp_path, monkeypatch, scenario)


def test_the_last_fantasy_week_runs_to_the_end_and_then_the_bot_stops(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        # A league whose championship is this week.
        world.sleeper.settings.update(playoff_week_start=4, playoff_teams=8)
        world.sleeper.bracket = []
        assert (await world.bot.fantasy()).last_week == WEEK

        await at(TUE_ANNOUNCE)
        assert "it is a fantasy playoff week" in channel.last()  # the championship week is on the commissioner
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)
        world.espn.set(TNF, state="post", home=24, away=20)
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(et(2026, 10, 18, 16, 30))
        assert "THE WEEK 6 PARLAY HIT" in channel.last()  # the final week is played out in full

        # Sleeper marks the league complete. From then on, nothing.
        channel.clear()
        world.sleeper.status = "complete"
        world.sleeper.settings["last_scored_leg"] = WEEK
        world.bot.forget_league()
        world.espn.finish_everything()
        await at(et(2026, 10, 20, 10, 1))
        await at(et(2026, 10, 27, 10, 1))
        assert channel.sent == []
        assert [row["week"] for row in await world.bot.db.parlays()] == [WEEK]

    play(tmp_path, monkeypatch, scenario)


def test_a_parlay_left_unfinished_when_the_fantasy_season_ends_is_settled_silently(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)

        # The bot is off while the games are played, and Sleeper now says the season ended a week ago.
        channel.clear()
        world.sleeper.status = "complete"
        world.sleeper.settings["last_scored_leg"] = WEEK - 1
        world.bot.forget_league()
        world.espn.set(TNF, state="post", home=24, away=20)
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        world.espn.finish_everything()
        await at(et(2026, 10, 20, 12, 0))

        assert channel.sent == []
        assert await world.statuses() == {1: "won", 2: "won"}
        assert (await world.week())["result"] == "won"

    play(tmp_path, monkeypatch, scenario)


def test_setup_and_status_wording():
    season = fantasy_season(league(), BRACKET_3_ROUNDS)
    assert season.span == "Weeks 1 to 17"
