"""Parsing real ESPN responses saved on 2026-10-11 (trimmed to the fields that are read)."""

from datetime import datetime, timezone

from longshot.clients.espn import Game, parse_boxscore, parse_game_roster, parse_scoreboard, parse_team_roster


def test_scoreboard(fixture):
    board = parse_scoreboard(fixture("espn_scoreboard_week4.json"))
    assert (board.season, board.week) == (2026, 4)
    assert len(board.games) == 16
    assert board.games == sorted(board.games, key=lambda game: game.kickoff)

    first = board.games[0]
    assert first.label == "PIT @ CLE"
    assert first.kickoff == datetime(2026, 10, 2, 0, 15, tzinfo=timezone.utc)
    assert (first.home.abbr, first.home.name, first.away.abbr) == ("CLE", "Cleveland Browns", "PIT")
    assert (first.home_score, first.away_score) == (27, 24)
    assert first.final and first.state == "post" and not first.called_off
    assert all(game.final for game in board.games)


def test_game_survives_a_round_trip_through_the_cache(fixture):
    game = parse_scoreboard(fixture("espn_scoreboard_week4.json")).games[0]
    assert Game.from_json(game.to_json()) == game


def test_boxscore_splits_paired_columns(fixture):
    box = parse_boxscore(fixture("espn_summary_401872980.json"))
    passer = box["4596472"]
    assert (passer.name, passer.team) == ("Jalon Daniels", "TB")
    assert passer.stats["passing.completions"] == 19
    assert passer.stats["passing.passingAttempts"] == 25
    assert passer.stats["passing.sacks"] == 1
    assert passer.stats["passing.sackYardsLost"] == 11
    kicker = box["3150744"]
    assert kicker.stats["kicking.fieldGoalsMade"] == 1
    assert kicker.stats["kicking.extraPointsMade"] == 3


def test_boxscore_keeps_categories_apart(fixture):
    # 'interceptions' is a passing column (thrown) and its own category (caught).
    box = parse_boxscore(fixture("espn_summary_401872980.json"))
    assert box["4596472"].stats["passing.interceptions"] == 0
    assert box["4686334"].stats["interceptions.interceptions"] == 1


def test_boxscore_of_a_game_not_started():
    assert parse_boxscore({}) == {}
    assert parse_boxscore({"boxscore": {}}) == {}


def test_team_roster_lists_pickable_positions_only(fixture):
    roster = parse_team_roster(fixture("espn_team_roster_phi.json"))
    by_name = {athlete.name: athlete for athlete in roster}
    assert by_name["Jalen Hurts"].position == "QB"
    assert by_name["Jalen Hurts"].id == "4040715"
    assert by_name["Saquon Barkley"].team == "PHI"
    assert {athlete.position for athlete in roster} <= {"QB", "RB", "FB", "WR", "TE", "PK"}


def test_game_roster_did_not_play(fixture):
    played = parse_game_roster(fixture("espn_game_roster.json"))
    assert len(played) == 57
    assert sum(1 for was_active in played.values() if not was_active) == 8
