"""/backfill: recording picks after the fact, for games already played."""

import pytest
from test_week_flow import IRVING_150, IRVING_TD, THU_DEADLINE, THU_LATE, TUE_ANNOUNCE, TUE_NOON, play
from world import BENCHED, BUCS_GAME, IRVING, SEASON, TNF, WEEK, et

from longshot.domain.lines import typed_line
from longshot.domain.markets import PROP_MARKETS
from longshot.domain.odds_math import parlay_decimal, total_return
from longshot.domain.stats import compute

AFTER_THE_WEEK = et(2026, 10, 20, 12, 0)
COMMISSIONER = 99
KICKER = "3150744"  # Chase McLaughlin: in the box score, not on the test roster


def played_out(world):
    """Week 6 is over: Denver beat Seattle 24-20 on Thursday; the Buccaneers game has its real box score."""
    world.espn.set(TNF, state="post", home=24, away=20)
    world.espn.set(BUCS_GAME, state="post", home=24, away=16)
    world.espn.finish_everything()
    return world.espn.games[TNF].espn_id, world.espn.games[BUCS_GAME].espn_id


# --- a line typed from the bet slip --------------------------------------------------

RUSH = PROP_MARKETS["player_rush_yds"]


def line(bet, text, **kwargs):
    made = typed_line(bet, text, -110, **kwargs)
    return made.market, made.side, made.point, made.alt, made.text


def test_typed_team_lines():
    assert line("h2h", None, team_abbr="DEN", is_home=True) == ("h2h", "home", None, False, "DEN moneyline")
    assert line("spreads", "-2.5", team_abbr="DEN", is_home=True) == ("spreads", "home", -2.5, False, "DEN -2.5")
    assert line("spreads", "+3", team_abbr="SEA") == ("spreads", "away", 3.0, False, "SEA +3")
    assert line("spreads", "pk", team_abbr="SEA") == ("spreads", "away", 0.0, False, "SEA PK")
    assert line("totals", "over 46.5") == ("totals", "over", 46.5, False, "Over 46.5")
    assert line("totals", "U43") == ("totals", "under", 43.0, False, "Under 43")


def test_typed_prop_lines():
    assert line("player_rush_yds", "Over 74.5", prop=RUSH) == ("player_rush_yds", "over", 74.5, False, "Over 74.5")
    assert line("player_rush_yds", "u74.5", prop=RUSH) == ("player_rush_yds", "under", 74.5, False, "Under 74.5")
    # "150+" on a slip is an alternate line: over 149.5.
    assert line("player_rush_yds", "150+", prop=RUSH) == ("player_rush_yds", "over", 149.5, True, "150+")
    touchdown = PROP_MARKETS["player_anytime_td"]
    assert line("player_anytime_td", "", prop=touchdown) == ("player_anytime_td", "yes", None, False, "Anytime TD")


@pytest.mark.parametrize(
    "bet, text, kwargs",
    [
        ("spreads", "", {"team_abbr": "DEN"}),
        ("spreads", "minus three", {"team_abbr": "DEN"}),
        ("spreads", "nan", {"team_abbr": "DEN"}),
        ("totals", "46.5", {}),  # over or under?
        ("totals", "47+", {}),
        ("player_rush_yds", "74.5", {"prop": RUSH}),
        ("player_rush_yds", "lots", {"prop": RUSH}),
        ("player_rush_yds", None, {"prop": RUSH}),
    ],
)
def test_typed_lines_that_are_refused(bet, text, kwargs):
    with pytest.raises(ValueError):
        typed_line(bet, text, -110, **kwargs)


# --- a week the bot never ran ------------------------------------------------------------


def test_backfilling_a_week_the_bot_never_ran(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog, db, channel = world.bot.get_cog("Backfill"), world.bot.db, world.channel
        tnf, bucs = played_out(world)
        await at(AFTER_THE_WEEK, tick=False)

        reply = await cog.record(COMMISSIONER, 1, WEEK, bucs, f"p:{IRVING}", "player_rush_yds", "+320", "150+")
        assert reply == (
            "Recorded <@1>'s Week 6 pick: **Bucky Irving 150+ rushing yards** (+320) · PIT @ TB. It **won**. "
            "(finished with 165.) Nothing was posted in the channel."
        )
        reply = await cog.record(COMMISSIONER, 2, WEEK, tnf, "t:DEN", "spreads", "-110", "-2.5")
        assert "**DEN -2.5** (-110) · SEA @ DEN. It **won**." in reply
        reply = await cog.record(COMMISSIONER, 3, WEEK, tnf, "t:SEA", "h2h", "+120")
        assert "**SEA moneyline** (+120) · SEA @ DEN. It **lost**." in reply

        # The parlay is on record as closed and lost, and nobody was told anything.
        week = await world.week()
        assert (week["backfilled"], bool(week["announced_at"]), bool(week["closed_at"])) == (1, True, True)
        assert (week["result"], week["payout"], bool(week["recap_at"])) == ("lost", None, True)
        assert (await db.pick(week["killer_pick_id"]))["discord_id"] == 3
        assert await world.statuses() == {1: "won", 2: "won", 3: "lost"}
        assert channel.sent == [] and world.dms == {}

        # Entering a member's pick again replaces it, and the result is worked out afresh.
        reply = await cog.record(COMMISSIONER, 3, WEEK, tnf, "t:DEN", "totals", "-110", "over 43.5")
        assert "**SEA @ DEN over 43.5 points** (-110)" in reply and "It **won**. (44 points.)" in reply
        assert "It replaces the pick they had." in reply
        week = await world.week()
        assert (week["result"], week["killer_pick_id"]) == ("won", None)
        assert week["payout"] == total_return(10, parlay_decimal([320, -110, -110]))

        # The clock running afterwards posts nothing about it.
        await at(et(2026, 10, 20, 12, 5))
        await at(et(2026, 10, 20, 12, 10))
        assert channel.sent == [] and world.dms == {}

        # It counts everywhere a normal week does.
        stats = compute(await db.members(), await db.all_picks(SEASON), await db.parlays(SEASON), await db.no_shows(SEASON))
        assert [(stats[n].legs, stats[n].won) for n in (1, 2, 3)] == [(1, 1), (1, 1), (1, 1)]
        slip = world.bot.get_cog("Slip")
        assert [(row["week"], row["result"]) for row in await slip._parlays(WEEK)] == [(WEEK, "won")]

    play(tmp_path, monkeypatch, scenario)


def test_every_kind_of_result(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog = world.bot.get_cog("Backfill")
        tnf, bucs = played_out(world)
        await at(AFTER_THE_WEEK, tick=False)

        async def record(*args):
            return await cog.record(COMMISSIONER, 1, WEEK, *args)

        # A kicker who is only in the box score, found by typing his name and the bet's name.
        assert "**Chase McLaughlin over 5.5 kicking points** (-120) · PIT @ TB. It **won**. (finished with 6.)" in (
            await record(bucs, "Chase McLaughlin", "Kicking points", "-120", "over 5.5")
        )
        assert "It was a **push**" in await record(bucs, f"p:{KICKER}", "player_kicking_points", "-120", "over 6")
        assert "It **lost**. (finished with 165.)" in await record(bucs, f"p:{IRVING}", "player_rush_yds", "-105", "under 74.5")
        assert "**Bucky Irving anytime TD** (-130)" in await record(bucs, f"p:{IRVING}", "player_anytime_td", "-130")
        assert "It is **void** (the player did not play)" in (
            await record(bucs, f"p:{BENCHED}", "player_rush_yds", "-110", "over 20.5")
        )
        assert "**SEA +6.5** (-190)" in await record(tnf, "t:SEA", "spreads", "-190", "+6.5")
        assert (await world.statuses()) == {1: "won"}  # Seattle lost by 4 and covered 6.5
        assert world.channel.sent == []

    play(tmp_path, monkeypatch, scenario)


def test_backfills_that_are_refused(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog = world.bot.get_cog("Backfill")
        tnf = world.espn.games[TNF].espn_id

        # Before the game has kicked off it is not a backfill.
        await at(TUE_NOON, tick=False)
        reply = await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:DEN", "h2h", "-140")
        assert "SEA @ DEN has not kicked off" in reply and "/pickfor" in reply

        tnf, bucs = played_out(world)
        await at(AFTER_THE_WEEK, tick=False)
        refusals = [
            (await cog.record(COMMISSIONER, 7, WEEK, tnf, "t:DEN", "h2h", "-140"), "not linked"),
            (await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:DEN", "h2h", "50"), "never between -100 and +100"),
            (await cog.record(COMMISSIONER, 1, WEEK, "999", "t:DEN", "h2h", "-140"), "Choose the game"),
            (await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:TB", "h2h", "-140"), "could not find that player or team"),
            (await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:DEN", "player_rush_yds", "-140"), "Choose the bet"),
            (await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:DEN", "spreads", "-110", "three"), "For a spread"),
            (await cog.record(COMMISSIONER, 1, WEEK, tnf, "t:DEN", "totals", "-110", "43.5"), "For a game total"),
            (await cog.record(COMMISSIONER, 1, WEEK, bucs, f"p:{IRVING}", "player_rush_yds", "-110"), "Give the line as"),
        ]
        for reply, expected in refusals:
            assert expected in reply, reply
        # None of that created anything.
        assert await world.bot.db.parlays() == [] and await world.bot.db.all_picks() == []

    play(tmp_path, monkeypatch, scenario)


# --- a parlay the bot is running ----------------------------------------------------------


def test_backfilling_a_leg_into_a_live_parlay_is_announced(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog, channel = world.bot.get_cog("Backfill"), world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *IRVING_TD)
        await at(THU_DEADLINE)  # member 3 missed the deadline
        world.espn.set(TNF, state="post", home=24, away=20)
        await at(THU_LATE)

        # On Friday the commissioner records the Thursday pick member 3 had made by hand.
        channel.clear()
        await at(et(2026, 10, 16, 12, 0), tick=False)
        tnf = world.espn.games[TNF].espn_id
        reply = await cog.record(COMMISSIONER, 3, WEEK, tnf, "t:DEN", "h2h", "-140")
        assert "It **won**." in reply and "Nothing was posted" not in reply
        assert channel.texts == [
            "📝 The commissioner entered a pick for <@3>: **DEN moneyline** (-140) · SEA @ DEN",
            "✅ **Leg hit** · <@3> · DEN moneyline (-140)",
        ]
        assert (await world.week())["backfilled"] == 0

        # The parlay carries on with three legs.
        channel.clear()
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(et(2026, 10, 18, 16, 30))
        expected = total_return(10, parlay_decimal([320, -130, -140]))
        assert "THE WEEK 6 PARLAY HIT" in channel.last() and f"${expected:,.2f}" in channel.last()

    play(tmp_path, monkeypatch, scenario)


def test_backfilling_a_losing_leg_into_a_live_parlay_kills_it(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog, channel = world.bot.get_cog("Backfill"), world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await at(THU_DEADLINE)
        world.espn.set(TNF, state="post", home=24, away=20)
        await at(THU_LATE)

        channel.clear()
        await at(et(2026, 10, 16, 12, 0), tick=False)
        await cog.record(COMMISSIONER, 2, WEEK, world.espn.games[TNF].espn_id, "t:SEA", "h2h", "+120")
        assert channel.texts[1] == "❌ **Leg missed** · <@2> · SEA moneyline (+120)"
        assert channel.texts[2].startswith("💀 **The Week 6 parlay is dead.** <@2>'s leg missed")

    play(tmp_path, monkeypatch, scenario)


def test_backfilling_into_a_week_the_bot_skipped(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog, channel = world.bot.get_cog("Backfill"), world.channel
        # The bot's first ever run is after the deadline, so it names the lowest scorer and skips the week.
        await at(et(2026, 10, 16, 12, 0))
        assert "deadline has already passed" in channel.last() and (await world.week())["skipped"] == 1
        channel.clear()

        tnf, bucs = played_out(world)
        await at(AFTER_THE_WEEK, tick=False)
        await cog.record(COMMISSIONER, 1, WEEK, bucs, f"p:{IRVING}", "player_rush_yds", "+320", "150+")
        await cog.record(COMMISSIONER, 2, WEEK, tnf, "t:DEN", "h2h", "-140")
        week = await world.week()
        assert (week["backfilled"], week["skipped"], week["result"]) == (1, 0, "won")
        assert week["loser_roster_id"] == 2  # the lowest scorer the bot had already named still stands
        await at(et(2026, 10, 20, 12, 5))
        assert channel.sent == []

    play(tmp_path, monkeypatch, scenario)


# --- the lists shown while filling the command in ---------------------------------------------


def test_no_game_is_offered_before_it_kicks_off(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog = world.bot.get_cog("Backfill")
        await at(TUE_NOON, tick=False)
        assert [c.name for c in await cog.game_choices(None, "")] == ["Choose the week first."]
        assert [c.name for c in await cog.game_choices(WEEK, "")] == ["No Week 6 game has kicked off yet."]
        # Thursday night, with the game under way, it appears.
        # The schedule saved a moment ago is made old, as it would be by Thursday.
        await world.bot.db.cache_put(f"espn:week:{SEASON}:{WEEK}", [], et(2020, 1, 1, 12, 0))
        world.espn.set(TNF, state="in", home=7, away=3)
        await at(et(2026, 10, 15, 21, 30), tick=False)
        assert [c.name for c in await cog.game_choices(WEEK, "")] == ["SEA @ DEN · Thu 8:15 PM · 3-7 (live)"]

    play(tmp_path, monkeypatch, scenario)


def test_backfill_autocomplete(tmp_path, monkeypatch):
    async def scenario(world, at):
        cog = world.bot.get_cog("Backfill")
        names = lambda choices: [choice.name for choice in choices]

        tnf, bucs = played_out(world)
        await at(AFTER_THE_WEEK, tick=False)
        games = await cog.game_choices(WEEK, "")
        assert len(games) == 14
        assert (games[0].name, games[0].value) == ("SEA @ DEN · Thu 8:15 PM · 20-24", tnf)
        assert names(await cog.game_choices(WEEK, "tb")) == ["PIT @ TB · Sun 1:00 PM · 16-24"]

        assert names(await cog.who_choices(WEEK, "", "")) == ["Choose the game first."]
        assert names(await cog.who_choices(WEEK, bucs, "")) == [
            "Pittsburgh Steelers · PIT @ TB Sun 1:00 PM", "Tampa Bay Buccaneers · PIT @ TB Sun 1:00 PM",
        ]
        assert names(await cog.who_choices(WEEK, bucs, "irv")) == ["Bucky Irving · RB · PIT @ TB Sun 1:00 PM"]
        kicker = await cog.who_choices(WEEK, bucs, "chase")
        assert [(c.name, c.value) for c in kicker] == [("Chase McLaughlin · PIT @ TB Sun 1:00 PM", f"p:{KICKER}")]

        assert names(await cog.bet_choices(WEEK, tnf, "t:DEN", "")) == ["Moneyline", "Spread", "Game total"]
        assert names(await cog.bet_choices(WEEK, bucs, f"p:{IRVING}", "rush"))[0] == "Rushing yards"
        assert names(await cog.bet_choices(WEEK, bucs, "", "")) == ["Choose the game and who first."]

        # The line field shows how what was typed will be read.
        assert names(cog.line_choices("spreads", "-2.5")) == ["Reads as: Team -2.5"]
        assert names(cog.line_choices("totals", "o46.5")) == ["Reads as: Game over 46.5 points"]
        assert names(cog.line_choices("player_rush_yds", "150+")) == ["Reads as: Player 150+ rushing yards"]
        assert cog.line_choices("player_rush_yds", " 150+ ")[0].value == "150+"
        assert names(cog.line_choices("player_rush_yds", "")) == [
            "Type the line from the bet slip: over 74.5, under 74.5 or 75+"
        ]
        assert names(cog.line_choices("totals", "46.5")) == ["For a game total, give the line as over 46.5 or under 46.5."]
        assert names(cog.line_choices("h2h", "")) == ["This bet has no line. Leave this empty."]
        assert names(cog.line_choices("player_anytime_td", "")) == ["This bet has no line. Leave this empty."]
        assert names(cog.line_choices("", "")) == ["Choose the bet first."]

    play(tmp_path, monkeypatch, scenario)
