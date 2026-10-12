from longshot import render
from longshot.domain.stats import MemberStats, compute, ranked

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


def test_lowest_scorer_is_whoever_held_the_team_at_the_time():
    # Roster 12 belonged to member 2 back then; member 1 has since taken it over.
    members = [{"discord_id": 1, "roster_id": 12}, {"discord_id": 2, "roster_id": 11}]
    recorded = [week(2, loser=12) | {"loser_discord_id": 2}]
    assert [compute(members, [], recorded, [])[n].low_scores for n in (1, 2)] == [0, 1]
    # A week saved without that detail falls back to whoever holds the team now.
    assert [compute(members, [], [week(2, loser=12)], [])[n].low_scores for n in (1, 2)] == [1, 0]
    # Someone who has left the league altogether still has their weeks counted.
    gone = [week(2, loser=12) | {"loser_discord_id": 9}]
    assert compute(members, [], gone, [])[9].low_scores == 1


def test_picks_from_someone_no_longer_linked_are_still_counted():
    stats = compute(MEMBERS[:2], PICKS, WEEKS, NO_SHOWS)
    assert stats[3].legs == 3
    assert stats[3].low_scores == 0  # the roster is no longer tied to them


# --- how the leaderboard is written out ---------------------------------------------


def test_leaderboard_is_short_lines_that_fit_a_phone():
    stats = compute(MEMBERS + [{"discord_id": 4, "roster_id": 14}], PICKS, WEEKS, NO_SHOWS)
    names = {1: "Team One", 2: "Team Two", 3: "Team Three", 4: "Team Four"}
    lines = render.leaderboard_lines(ranked(stats), names)
    assert lines == [
        "🥇 **Team One** · **100%** (3-0)",
        "└ 🔥 3 in a row",
        "🥈 **Team Two** · **50%** (1-1)",
        "└ 💀 killed 1 · 📉 low score 2 · ⏰ missed 1",
        "🥉 **Team Three** · **50%** (1-1)",
        "└ 💀 killed 1 · 📉 low score 1 · ⏰ missed 1",
        "**4.** **Team Four** · no legs yet",
    ]
    # No table and no code block: a phone wraps ordinary text between words, not mid-row.
    assert not any("```" in line for line in lines)
    assert max(len(line) for line in lines) <= 48


def test_leaderboard_trims_long_team_names_and_omits_empty_details():
    entries = [MemberStats(1, legs=4, won=2, lost=2, streak=1), MemberStats(2, legs=1, won=0, lost=1)]
    names = {1: "The Team With An Extremely Long Name"}
    assert render.leaderboard_lines(entries, names) == [
        "🥇 **The Team With An Extrem…** · **50%** (2-2)",  # 24 characters; and a run of one is no streak
        "🥈 **Unlinked member** · **0%** (0-1)",
    ]


def test_leaderboard_for_a_full_league_fits_in_one_message():
    entries = [
        MemberStats(n, legs=17, won=10, lost=7, streak=4, kills=3, low_scores=2, no_shows=1) for n in range(1, 13)
    ]
    names = {n: "A Fairly Long Team Name!" for n in range(1, 13)}
    text = "\n".join(render.leaderboard_lines(entries, names))
    assert len(text) < 4096  # Discord's limit for the body of a card
