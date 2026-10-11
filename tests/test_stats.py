from longshot.domain.stats import compute, ranked

MEMBERS = [
    {"discord_id": 1, "roster_id": 11},
    {"discord_id": 2, "roster_id": 12},
    {"discord_id": 3, "roster_id": 13},
]


def pick(pick_id, week, discord_id, status):
    return {"id": pick_id, "season": 2026, "week": week, "discord_id": discord_id, "status": status}


def week(number, killer=None, loser=None):
    return {"season": 2026, "week": number, "killer_pick_id": killer, "loser_roster_id": loser}


PICKS = [
    pick(1, 2, 1, "won"), pick(2, 2, 2, "lost"), pick(3, 2, 3, "won"),
    pick(4, 3, 1, "won"), pick(5, 3, 2, "push"), pick(6, 3, 3, "lost"),
    pick(7, 4, 1, "push"), pick(8, 4, 2, "won"), pick(9, 4, 3, "pending"),
    pick(10, 5, 1, "won"),
]
WEEKS = [week(2, killer=2, loser=12), week(3, killer=6, loser=12), week(4, loser=13), week(5)]
NO_SHOWS = [{"season": 2026, "week": 5, "discord_id": 2}, {"season": 2026, "week": 5, "discord_id": 3}]


def test_records_and_hit_rate():
    stats = compute(MEMBERS, PICKS, WEEKS, NO_SHOWS)
    one, two, three = stats[1], stats[2], stats[3]
    assert (one.legs, one.won, one.lost, one.dropped, one.unsettled) == (4, 3, 0, 1, 0)
    assert one.hit_rate == 1.0
    assert (two.won, two.lost, two.dropped) == (1, 1, 1)
    assert two.hit_rate == 0.5
    assert (three.won, three.lost, three.unsettled) == (1, 1, 1)


def test_a_push_neither_extends_nor_breaks_a_streak():
    stats = compute(MEMBERS, PICKS, WEEKS, NO_SHOWS)
    assert (stats[1].streak, stats[1].best_streak) == (3, 3)  # won, won, push, won
    assert (stats[2].streak, stats[2].best_streak) == (1, 1)
    assert (stats[3].streak, stats[3].best_streak) == (0, 1)  # the miss in week 3 ended it


def test_kills_low_scores_and_no_shows():
    stats = compute(MEMBERS, PICKS, WEEKS, NO_SHOWS)
    assert [stats[i].kills for i in (1, 2, 3)] == [0, 1, 1]
    assert [stats[i].low_scores for i in (1, 2, 3)] == [0, 2, 1]
    assert [stats[i].no_shows for i in (1, 2, 3)] == [0, 1, 1]


def test_ranking_puts_members_with_no_decided_legs_last():
    stats = compute(MEMBERS + [{"discord_id": 4, "roster_id": 14}], PICKS, WEEKS, NO_SHOWS)
    assert [entry.discord_id for entry in ranked(stats)] == [1, 2, 3, 4]
    assert stats[4].hit_rate is None


def test_picks_from_someone_no_longer_linked_are_still_counted():
    stats = compute(MEMBERS[:2], PICKS, WEEKS, NO_SHOWS)
    assert stats[3].legs == 3
    assert stats[3].low_scores == 0  # the roster is no longer tied to them
