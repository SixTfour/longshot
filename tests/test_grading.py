"""Grading, checked against a real box score: Buccaneers 24, Cowboys 16 (ESPN event 401872980)."""

import pytest

from longshot.clients.espn import parse_boxscore, parse_game_roster
from longshot.domain.grading import (
    LOST,
    PENDING,
    PUSH,
    REVIEW,
    VOID,
    WON,
    grade_prop,
    grade_team_bet,
    parlay_status,
)
from longshot.domain.markets import PROP_MARKETS

IRVING = "4596448"  # 21 carries, 165 yards, 1 rushing TD; 2 catches, 10 yards, 1 receiving TD
DANIELS = "4596472"  # 19 of 25, 189 yards, 1 TD, 0 INT; 7 carries, 23 yards
MCLAUGHLIN = "3150744"  # 1 field goal, 3 extra points, 6 kicking points
TEZ_JOHNSON = "4608810"  # 1 catch, 64 yards, no TD
DIABY = "4686334"  # defender with an interception, no TD

HOME, AWAY = 16, 24  # Dallas hosted Tampa Bay


@pytest.fixture
def box(fixture):
    return parse_boxscore(fixture("espn_summary_401872980.json"))


def prop(box, key, player, side, line, final=True):
    return grade_prop(PROP_MARKETS[key], side, line, box[player].stats, final)


# --- team bets ---------------------------------------------------------------


@pytest.mark.parametrize(
    "side, expected",
    [("away", WON), ("home", LOST)],
)
def test_moneyline(side, expected):
    assert grade_team_bet("h2h", side, None, HOME, AWAY, final=True).status == expected


def test_moneyline_tie_is_a_push():
    assert grade_team_bet("h2h", "home", None, 20, 20, final=True).status == PUSH


@pytest.mark.parametrize(
    "side, line, expected",
    [
        ("away", -7.5, WON),  # won by 8
        ("away", -8.0, PUSH),
        ("away", -8.5, LOST),
        ("home", 8.5, WON),
        ("home", 8.0, PUSH),
        ("home", 7.5, LOST),
        ("home", -3.0, LOST),
    ],
)
def test_spread_including_alternates(side, line, expected):
    assert grade_team_bet("spreads", side, line, HOME, AWAY, final=True).status == expected


@pytest.mark.parametrize(
    "side, line, expected",
    [("over", 39.5, WON), ("under", 39.5, LOST), ("over", 40.0, PUSH), ("under", 40.0, PUSH), ("over", 46.5, LOST), ("under", 46.5, WON)],
)
def test_total_including_alternates(side, line, expected):
    grade = grade_team_bet("totals", side, line, HOME, AWAY, final=True)
    assert grade.status == expected
    assert grade.value == 40


def test_team_bets_wait_for_the_final_whistle():
    assert grade_team_bet("h2h", "away", None, 0, 21, final=False).status == PENDING
    assert grade_team_bet("spreads", "away", -3.5, 0, 21, final=False).status == PENDING
    assert grade_team_bet("totals", "under", 46.5, 10, 14, final=False).status == PENDING


def test_a_total_already_over_is_decided_early():
    assert grade_team_bet("totals", "over", 39.5, 16, 24, final=False).status == WON
    assert grade_team_bet("totals", "under", 39.5, 16, 24, final=False).status == LOST


# --- player props against the real box score ----------------------------------


@pytest.mark.parametrize(
    "key, player, value",
    [
        ("player_pass_yds", DANIELS, 189),
        ("player_pass_tds", DANIELS, 1),
        ("player_pass_completions", DANIELS, 19),
        ("player_pass_attempts", DANIELS, 25),
        ("player_pass_interceptions", DANIELS, 0),
        ("player_pass_rush_yds", DANIELS, 212),
        ("player_rush_yds", IRVING, 165),
        ("player_rush_attempts", IRVING, 21),
        ("player_receptions", IRVING, 2),
        ("player_reception_yds", IRVING, 10),
        ("player_rush_reception_yds", IRVING, 175),
        ("player_kicking_points", MCLAUGHLIN, 6),
        ("player_field_goals", MCLAUGHLIN, 1),
        ("player_anytime_td", IRVING, 2),
        ("player_anytime_td", TEZ_JOHNSON, 0),
        ("player_anytime_td", DANIELS, 0),  # a passing TD is not a TD scored
        ("player_anytime_td", DIABY, 0),
    ],
)
def test_every_prop_reads_the_right_stat(box, key, player, value):
    assert PROP_MARKETS[key].stat(box[player].stats) == value


def test_main_line_over_and_under(box):
    assert prop(box, "player_rush_yds", IRVING, "over", 74.5).status == WON
    assert prop(box, "player_rush_yds", IRVING, "under", 74.5).status == LOST
    assert prop(box, "player_pass_yds", DANIELS, "over", 224.5).status == LOST
    assert prop(box, "player_pass_yds", DANIELS, "under", 224.5).status == WON


def test_alternate_lines_grade_like_any_other_line(box):
    # "150+" is stored as over 149.5, "175+" as over 174.5.
    assert prop(box, "player_rush_yds", IRVING, "over", 149.5).status == WON
    assert prop(box, "player_rush_yds", IRVING, "over", 174.5).status == LOST
    assert prop(box, "player_receptions", IRVING, "over", 1.5).status == WON
    assert prop(box, "player_receptions", IRVING, "over", 2.5).status == LOST


def test_landing_on_a_whole_number_line_is_a_push(box):
    grade = prop(box, "player_kicking_points", MCLAUGHLIN, "over", 6.0)
    assert (grade.status, grade.value) == (PUSH, 6)


def test_anytime_td(box):
    assert prop(box, "player_anytime_td", IRVING, "yes", None).status == WON
    assert prop(box, "player_anytime_td", TEZ_JOHNSON, "yes", None).status == LOST
    assert prop(box, "player_anytime_td", TEZ_JOHNSON, "yes", None, final=False).status == PENDING
    assert prop(box, "player_anytime_td", IRVING, "yes", None, final=False).status == WON


def test_counting_stats_settle_early_but_yardage_waits(box):
    # Receptions cannot go down, so 2 catches against 1.5 is decided mid-game.
    assert prop(box, "player_receptions", IRVING, "over", 1.5, final=False).status == WON
    assert prop(box, "player_receptions", IRVING, "under", 1.5, final=False).status == LOST
    # Rushing yards can fall on a loss, so even 165 against 74.5 waits.
    assert prop(box, "player_rush_yds", IRVING, "over", 74.5, final=False).status == PENDING
    assert prop(box, "player_rush_yds", IRVING, "under", 74.5, final=False).status == PENDING
    # Not yet over the line: still pending.
    assert prop(box, "player_receptions", IRVING, "over", 4.5, final=False).status == PENDING


def test_player_with_no_stat_line(box, fixture):
    rush = PROP_MARKETS["player_rush_yds"]
    assert grade_prop(rush, "over", 20.5, None, final=False).status == PENDING
    assert grade_prop(rush, "over", 20.5, None, final=True, played=False).status == VOID
    assert grade_prop(rush, "over", 20.5, None, final=True, played=None).status == REVIEW
    # On the field but never touched the ball: zero yards, so the over loses.
    played_no_stats = grade_prop(rush, "over", 20.5, None, final=True, played=True)
    assert (played_no_stats.status, played_no_stats.value) == (LOST, 0)
    assert grade_prop(rush, "under", 20.5, None, final=True, played=True).status == WON


def test_did_not_play_flags_never_contradict_the_box_score(box, fixture):
    played = parse_game_roster(fixture("espn_game_roster.json"))
    sat_out = [athlete for athlete, was_active in played.items() if not was_active]
    assert sat_out and not any(athlete in box for athlete in sat_out)


# --- the slip as a whole -------------------------------------------------------


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ([WON, WON, WON], WON),
        ([WON, PENDING, WON], PENDING),
        ([WON, LOST, PENDING], LOST),
        ([WON, PUSH, VOID], WON),
        ([PUSH, VOID], VOID),
        ([WON, REVIEW], PENDING),
        ([], PENDING),
    ],
)
def test_parlay_status(statuses, expected):
    assert parlay_status(statuses) == expected
