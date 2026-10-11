from longshot.domain.loser import lowest_scorers, roster_scores


def entry(roster_id, points, custom_points=None):
    return {"roster_id": roster_id, "matchup_id": 1, "points": points, "custom_points": custom_points}


def test_lowest_scorer():
    assert lowest_scorers([entry(1, 112.4), entry(2, 78.32), entry(3, 99.0)]) == (78.32, [2])


def test_tie_returns_every_roster_on_the_low_score():
    assert lowest_scorers([entry(1, 80.0), entry(2, 80.0), entry(3, 99.0)]) == (80.0, [1, 2])


def test_commissioner_override_replaces_points():
    matchups = [entry(1, 60.0, custom_points=95.0), entry(2, 78.32), entry(3, 99.0)]
    assert roster_scores(matchups)[1] == 95.0
    assert lowest_scorers(matchups) == (78.32, [2])


def test_override_of_zero_is_respected():
    assert lowest_scorers([entry(1, 60.0, custom_points=0.0), entry(2, 78.32)]) == (0.0, [1])


def test_week_without_scores():
    assert lowest_scorers([]) is None
    assert lowest_scorers([entry(1, 0.0), entry(2, 0.0)]) is None
    assert lowest_scorers([entry(1, None)]) is None
