"""The slip: a progress bar per leg, and one message that is edited in place as the games are played."""

from test_week_flow import (
    DEN_SPREAD,
    IRVING_150,
    IRVING_TD,
    SUN_EARLY_DONE,
    SUN_EARLY_LIVE,
    THU_DEADLINE,
    THU_HALFTIME,
    THU_LATE,
    TUE_ANNOUNCE,
    TUE_NOON,
    play,
)
from world import BUCS_GAME, TNF

from longshot import render

FULL, EMPTY = "▰" * 10, "▱" * 10


def leg(status="pending", value=None, **fields):
    base = dict(
        discord_id=1, label="Jalen Hurts 40+ rushing yards", price=105, status=status, stat_value=value, note=None,
        market="player_rush_yds", side="over", line=39.5, alt=1, player_id="1", team_abbr=None,
        espn_event_id="g1", kickoff="2026-10-18T17:00:00+00:00",
    )
    return base | fields


def team_leg(market, line, value, status="pending"):
    return leg(status, value, market=market, side="home", line=line, alt=0, player_id=None, team_abbr="PHI")


# --- the bar ----------------------------------------------------------------------


def test_bar():
    assert render.bar(0, 40) == EMPTY
    assert render.bar(20, 40) == "▰▰▰▰▰▱▱▱▱▱"
    assert render.bar(39, 40) == "▰▰▰▰▰▰▰▰▰▱"  # one short is never shown as full
    assert render.bar(40, 40) == FULL
    assert render.bar(165, 150) == FULL
    assert render.bar(-3, 40) == EMPTY  # a runner can be on negative yards


# --- a leg's progress line ----------------------------------------------------------


def test_a_leg_whose_game_has_not_started_shows_the_kickoff():
    assert render.progress(leg()) == "Sun 1:00 PM ET"


def test_over_legs_fill_toward_the_line():
    assert render.progress(leg(value=31)) == "▰▰▰▰▰▰▰▱▱▱  31 / 40"  # a 40+ line reads as on the slip
    main = leg(value=48, line=74.5, alt=0)
    assert render.progress(main) == "▰▰▰▰▰▰▱▱▱▱  48 / 74.5"
    assert render.progress(leg("won", 52)) == f"{FULL}  52 / 40"
    assert render.progress(leg("lost", 31)) == "▰▰▰▰▰▰▰▱▱▱  31 / 40"
    total = leg(value=37, market="totals", line=46.5, alt=0, player_id=None)
    assert render.progress(total) == "▰▰▰▰▰▰▰▱▱▱  37 / 46.5"


def test_under_legs_fill_as_the_room_runs_out():
    under = dict(side="under", line=5.5, alt=0, market="player_receptions")
    assert render.progress(leg(value=4, **under)) == "▰▰▰▰▰▰▱▱▱▱  4 so far · 1 to spare"
    assert render.progress(leg(value=0, **under)) == f"{EMPTY}  0 so far · 5 to spare"
    assert render.progress(leg("lost", 6, **under)) == f"{FULL}  6 · over the line"
    assert render.progress(leg("won", 4, **under)) == "▰▰▰▰▰▰▱▱▱▱  finished with 4"


def test_team_legs_show_the_score_not_a_bar():
    assert render.progress(team_leg("spreads", -2.5, 7)) == "PHI by 7 · covering"
    assert render.progress(team_leg("spreads", -2.5, 2)) == "PHI by 2 · not covering"
    assert render.progress(team_leg("spreads", 3.0, -3)) == "PHI down 3 · on the number"
    assert render.progress(team_leg("spreads", 6.5, -3)) == "PHI down 3 · covering"
    assert render.progress(team_leg("spreads", -2.5, 7, "won")) == "PHI won by 7 · covered"
    assert render.progress(team_leg("spreads", -2.5, 1, "lost")) == "PHI won by 1 · did not cover"
    assert render.progress(team_leg("h2h", None, 0)) == "tied"
    assert render.progress(team_leg("h2h", None, -3, "lost")) == "PHI lost by 3"


def test_other_legs():
    touchdown = dict(market="player_anytime_td", side="yes", line=None, alt=0)
    assert render.progress(leg(value=0, **touchdown)) == "no TD yet"
    assert render.progress(leg("won", 1, **touchdown)) == "scored"
    assert render.progress(leg("lost", 0, **touchdown)) == "no TD"
    assert render.progress(leg("push", 40)) == "push"
    assert render.progress(leg("void", note="Did not play")) == "Did not play"
    assert render.progress(leg("review")) == "needs a ruling: `/grade`"


def test_a_leg_is_two_lines_on_the_slip():
    assert render.slip_block(leg(value=31)).splitlines() == [
        "⏳ <@1> · **Jalen Hurts 40+ rushing yards** (+105)",
        "└ ▰▰▰▰▰▰▰▱▱▱  31 / 40",
    ]


def test_parlay_bar_has_a_square_per_leg():
    legs = [leg("won", 52), leg("lost", 10), leg(value=12), leg(), leg("push", 40), leg("review")]
    # Hit, missed, in play, not started, dropped, needs a ruling. A dropped leg is not counted.
    assert render.parlay_bar(legs) == "🟩🟥🟨⬜⬛🟪  1 of 5 legs in"


# --- the live slip message ------------------------------------------------------------


def slip_lines(world, message_id):
    return world.channel.message(message_id).embed.description.splitlines()


def test_the_slip_is_edited_in_place_as_games_are_played(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)

        # The slip posted when picks close is the one that will be kept up to date.
        slip_id = (await world.week())["slip_message_id"]
        assert slip_id == channel.sent[-1].id
        assert slip_lines(world, slip_id) == [
            "⬜⬜  0 of 2 legs in",
            "",
            "⏳ <@2> · **DEN -2.5** (-110)",
            "└ Thu 8:15 PM ET",
            "⏳ <@1> · **Bucky Irving 150+ rushing yards** (+320)",
            "└ Sun 1:00 PM ET",
        ]

        # Thursday night, Denver up 7-3: the slip changes, and nothing new is posted.
        posted = len(channel.sent)
        world.espn.set(TNF, state="in", home=7, away=3)
        await at(THU_HALFTIME)
        assert len(channel.edits) == 1 and channel.edits[0].id == slip_id
        assert slip_lines(world, slip_id)[:4] == [
            "🟨⬜  0 of 2 legs in", "", "⏳ <@2> · **DEN -2.5** (-110)", "└ DEN by 4 · covering",
        ]
        assert len(channel.sent) == posted
        await at(THU_HALFTIME)
        assert len(channel.edits) == 1  # nothing changed, so it is not edited again

        world.espn.set(TNF, state="post", home=24, away=20)
        await at(THU_LATE)
        assert slip_lines(world, slip_id)[:4] == [
            "🟩⬜  1 of 2 legs in", "", "✅ <@2> · **DEN -2.5** (-110)", "└ DEN won by 4 · covered",
        ]

        # Sunday, mid-game: the yardage leg is past its line but waits for the whistle.
        world.espn.set(BUCS_GAME, state="in", home=14, away=10)
        await at(SUN_EARLY_LIVE)
        assert slip_lines(world, slip_id)[0] == "🟩🟨  1 of 2 legs in"
        assert slip_lines(world, slip_id)[-2:] == [
            "⏳ <@1> · **Bucky Irving 150+ rushing yards** (+320)", "└ ▰▰▰▰▰▰▰▰▰▰  165 / 150",
        ]

        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert slip_lines(world, slip_id)[0] == "🟩🟩  2 of 2 legs in"
        assert channel.message(slip_id).embed.colour.value == render.GREEN
        assert "THE WEEK 6 PARLAY HIT" in channel.last()  # the win is its own message, as before
        assert all(edit.id == slip_id for edit in channel.edits)

    play(tmp_path, monkeypatch, scenario)


def test_a_late_pick_joins_the_posted_slip(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await at(THU_DEADLINE)
        slip_id = (await world.week())["slip_message_id"]
        assert slip_lines(world, slip_id)[0] == "⬜  0 of 1 legs in"

        await world.pick(3, *IRVING_TD, by=99)
        assert slip_lines(world, slip_id)[0] == "⬜⬜  0 of 2 legs in"
        assert "⏳ <@3> · **Bucky Irving anytime TD** (-130)" in slip_lines(world, slip_id)
        assert world.channel.message(slip_id).embed.fields[0].name == "2 legs"

    play(tmp_path, monkeypatch, scenario)


def test_a_dead_parlays_slip_still_updates_but_nothing_is_posted(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)
        slip_id = (await world.week())["slip_message_id"]

        world.espn.set(TNF, state="post", home=21, away=20)  # Denver does not cover
        await at(THU_LATE)
        assert slip_lines(world, slip_id)[0] == "🟥⬜  0 of 2 legs in"
        assert channel.message(slip_id).embed.colour.value == render.RED

        channel.clear()
        edits = len(channel.edits)
        world.espn.set(BUCS_GAME, state="in", home=14, away=10)
        await at(SUN_EARLY_LIVE)
        assert channel.sent == []  # no message: the parlay is dead
        assert len(channel.edits) == edits + 1  # but the slip itself stays current
        assert slip_lines(world, slip_id)[-1] == "└ ▰▰▰▰▰▰▰▰▰▰  165 / 150"

    play(tmp_path, monkeypatch, scenario)
