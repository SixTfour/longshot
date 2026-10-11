"""Building pick lists from odds-feed markets.

The event below is hand-written in the response shape The Odds API documents
(bookmakers > markets > outcomes with name, description, price, point). It is
not a recorded response.
"""

from longshot.domain.lines import (
    bookmaker_markets,
    conflict,
    filter_lines,
    find_line,
    pick_label,
    player_lines,
    team_lines,
)
from longshot.domain.markets import PROP_MARKETS


def outcome(name, price, point=None, description=None):
    data = {"name": name, "price": price}
    if point is not None:
        data["point"] = point
    if description is not None:
        data["description"] = description
    return data


EVENT = {
    "id": "abc123",
    "home_team": "Jacksonville Jaguars",
    "away_team": "Philadelphia Eagles",
    "bookmakers": [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [outcome("Philadelphia Eagles", -999)]}]},
        {
            "key": "draftkings",
            "markets": [
                {"key": "h2h", "outcomes": [outcome("Philadelphia Eagles", -150), outcome("Jacksonville Jaguars", 130)]},
                {
                    "key": "spreads",
                    "outcomes": [outcome("Philadelphia Eagles", -110, -2.5), outcome("Jacksonville Jaguars", -110, 2.5)],
                },
                {
                    "key": "alternate_spreads",
                    "outcomes": [
                        outcome("Philadelphia Eagles", -110, -2.5),
                        outcome("Philadelphia Eagles", 180, -6.5),
                        outcome("Philadelphia Eagles", -240, 3.5),
                        outcome("Philadelphia Eagles", 100, -3.0),
                        outcome("Jacksonville Jaguars", -260, 7.5),
                    ],
                },
                {"key": "totals", "outcomes": [outcome("Over", -118, 46.5), outcome("Under", -102, 46.5)]},
                {
                    "key": "alternate_totals",
                    "outcomes": [outcome("Over", 150, 50.5), outcome("Under", -190, 50.5), outcome("Over", -210, 42.5)],
                },
                {
                    "key": "player_rush_yds",
                    "outcomes": [
                        outcome("Over", -115, 34.5, "Jalen Hurts"),
                        outcome("Under", -105, 34.5, "Jalen Hurts"),
                        outcome("Over", -112, 74.5, "Saquon Barkley"),
                    ],
                },
                {
                    "key": "player_rush_yds_alternate",
                    "outcomes": [
                        outcome("Over", 190, 49.5, "Jalen Hurts"),
                        outcome("Over", -240, 24.5, "Jalen Hurts"),
                        outcome("Over", 105, 39.5, "Jalen Hurts"),
                        outcome("Over", -115, 34.5, "Jalen Hurts"),
                        outcome("Over", 300, 99.5, "Saquon Barkley"),
                    ],
                },
                {
                    "key": "player_anytime_td",
                    "outcomes": [outcome("Yes", 120, description="Jalen Hurts"), outcome("Yes", -140, description="Saquon Barkley")],
                },
            ],
        },
    ],
}

MARKETS = bookmaker_markets(EVENT, "draftkings")
PHI = dict(team_name="Philadelphia Eagles", team_abbr="PHI", is_home=False)
JAX = dict(team_name="Jacksonville Jaguars", team_abbr="JAX", is_home=True)
RUSH = PROP_MARKETS["player_rush_yds"]


def choices(lines):
    return [line.choice for line in lines]


def test_only_the_chosen_bookmaker_is_read():
    assert bookmaker_markets(EVENT, "fanduel")["h2h"][0]["price"] == -999
    assert bookmaker_markets(EVENT, "betmgm") == {}
    assert bookmaker_markets(None, "draftkings") == {}


def test_moneyline_is_the_chosen_team_only():
    lines = team_lines(MARKETS, "h2h", **PHI)
    assert choices(lines) == ["PHI moneyline (-150)"]
    assert (lines[0].side, lines[0].point) == ("away", None)
    assert choices(team_lines(MARKETS, "h2h", **JAX)) == ["JAX moneyline (+130)"]


def test_spread_lists_main_line_first_then_nearest_alternates():
    lines = team_lines(MARKETS, "spreads", **PHI)
    assert choices(lines) == ["PHI -2.5 (-110)", "PHI -3 (+100)", "PHI -6.5 (+180)", "PHI +3.5 (-240)"]
    assert [line.alt for line in lines] == [False, True, True, True]
    assert all(line.side == "away" and line.market == "spreads" for line in lines)


def test_spread_for_the_other_team_has_its_own_lines():
    assert choices(team_lines(MARKETS, "spreads", **JAX)) == ["JAX +2.5 (-110)", "JAX +7.5 (-260)"]


def test_game_total_merges_alternates():
    lines = team_lines(MARKETS, "totals", **PHI)
    assert choices(lines) == [
        "Over 46.5 (-118)",
        "Under 46.5 (-102)",
        "Over 42.5 (-210)",
        "Over 50.5 (+150)",
        "Under 50.5 (-190)",
    ]


def test_player_prop_merges_main_and_alternate_lines():
    lines = player_lines(MARKETS, RUSH, "Jalen Hurts")
    assert choices(lines) == ["Over 34.5 (-115)", "Under 34.5 (-105)", "25+ (-240)", "40+ (+105)", "50+ (+190)"]
    # The alternate market repeats the main 34.5; it appears once, as the main line.
    assert [line.alt for line in lines] == [False, False, True, True, True]
    assert all(line.market == "player_rush_yds" for line in lines)


def test_player_is_matched_despite_spelling_differences():
    assert len(player_lines(MARKETS, RUSH, "Saquon Barkley Jr.")) == 2


def test_no_line_for_a_player_the_feed_does_not_list():
    assert player_lines(MARKETS, RUSH, "Trevor Lawrence") == []
    assert player_lines({}, RUSH, "Jalen Hurts") == []


def test_anytime_td_is_a_single_yes_line():
    lines = player_lines(MARKETS, PROP_MARKETS["player_anytime_td"], "Jalen Hurts")
    assert choices(lines) == ["Anytime TD (+120)"]
    assert (lines[0].side, lines[0].point, lines[0].key) == ("yes", None, "yes")


def test_typing_narrows_the_list():
    lines = player_lines(MARKETS, RUSH, "Jalen Hurts")
    # "40" finds the 40+ line, not the -240 price on another line.
    assert choices(filter_lines(lines, "40")) == ["40+ (+105)"]
    assert choices(filter_lines(lines, "under")) == ["Under 34.5 (-105)"]
    assert choices(filter_lines(lines, "-240")) == ["25+ (-240)"]
    assert len(filter_lines(lines, "")) == 5


def test_find_line_by_key_or_exact_wording():
    lines = player_lines(MARKETS, RUSH, "Jalen Hurts")
    assert find_line(lines, "over:39.5").choice == "40+ (+105)"
    assert find_line(lines, "50+").choice == "50+ (+190)"
    assert find_line(lines, "over 34.5").choice == "Over 34.5 (-115)"
    assert find_line(lines, "Under 34.5 (-105)").choice == "Under 34.5 (-105)"
    assert find_line(lines, "over") is None  # half-typed text never becomes a bet
    assert find_line(lines, "4") is None
    assert find_line(lines, "over:999.5") is None


def test_pick_labels():
    hurts = player_lines(MARKETS, RUSH, "Jalen Hurts")
    label = lambda line: pick_label(line, game="PHI @ JAX", player="Jalen Hurts", noun=RUSH.noun)
    assert label(hurts[0]) == "Jalen Hurts over 34.5 rushing yards"
    assert label(hurts[3]) == "Jalen Hurts 40+ rushing yards"
    td = PROP_MARKETS["player_anytime_td"]
    td_line = player_lines(MARKETS, td, "Jalen Hurts")[0]
    assert pick_label(td_line, game="PHI @ JAX", player="Jalen Hurts", noun=td.noun) == "Jalen Hurts anytime TD"
    assert pick_label(team_lines(MARKETS, "spreads", **PHI)[0], game="PHI @ JAX") == "PHI -2.5"
    assert pick_label(team_lines(MARKETS, "h2h", **PHI)[0], game="PHI @ JAX") == "PHI moneyline"
    assert pick_label(team_lines(MARKETS, "totals", **PHI)[0], game="PHI @ JAX") == "PHI @ JAX over 46.5 points"


def leg(market, side, line=None, player_id=None, event="1"):
    return {"espn_event_id": event, "market": market, "side": side, "line": line, "player_id": player_id}


def test_conflicts_between_picks():
    assert conflict(leg("h2h", "home"), leg("h2h", "home")) == "same"
    assert conflict(leg("h2h", "home"), leg("h2h", "away")) == "opposite"
    assert conflict(leg("spreads", "away", -2.5), leg("spreads", "away", -2.5)) == "same"
    assert conflict(leg("spreads", "away", -2.5), leg("spreads", "home", 2.5)) == "opposite"
    assert conflict(leg("spreads", "away", -2.5), leg("spreads", "away", -6.5)) is None
    assert conflict(leg("spreads", "away", -2.5), leg("spreads", "home", 7.5)) is None
    assert conflict(leg("totals", "over", 46.5), leg("totals", "under", 46.5)) == "opposite"
    assert conflict(leg("totals", "over", 46.5), leg("totals", "over", 50.5)) is None
    assert conflict(leg("player_rush_yds", "over", 34.5, "1"), leg("player_rush_yds", "over", 34.5, "1")) == "same"
    assert conflict(leg("player_rush_yds", "over", 34.5, "1"), leg("player_rush_yds", "under", 34.5, "1")) == "opposite"
    assert conflict(leg("player_rush_yds", "over", 34.5, "1"), leg("player_rush_yds", "over", 34.5, "2")) is None
    assert conflict(leg("player_anytime_td", "yes", None, "1"), leg("player_anytime_td", "yes", None, "1")) == "same"
    assert conflict(leg("h2h", "home"), leg("h2h", "home", event="2")) is None
    assert conflict(leg("h2h", "home"), leg("spreads", "home", -2.5)) is None
