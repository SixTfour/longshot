"""A whole week played out against the fakes in world.py: announce, pick, remind, close, track, recap."""

import asyncio
import json
import sys

import pytest
from discord import app_commands
from world import BENCHED, BUCS_GAME, DANIELS, IRVING, SEASON, TNF, WEEK, FakeInteraction, build, et, user

from longshot import tracking
from longshot.domain.cards import MAIN
from longshot.domain.injuries import INJURED, NOT_INJURED, InjuryNote
from longshot.domain.odds_math import parlay_decimal, total_return
from longshot.domain.stats import compute
from longshot.linebook import NoCredits

TUE_ANNOUNCE = et(2026, 10, 13, 10, 1)
TUE_NOON = et(2026, 10, 13, 12, 0)
WED_EVENING = et(2026, 10, 14, 19, 1)
THU_DEADLINE = et(2026, 10, 15, 19, 1)
THU_HALFTIME = et(2026, 10, 15, 21, 30)
THU_LATE = et(2026, 10, 15, 23, 45)
SUN_EARLY_LIVE = et(2026, 10, 18, 14, 0)
SUN_EARLY_DONE = et(2026, 10, 18, 16, 30)

IRVING_150 = (f"p:{IRVING}", "player_rush_yds", "over:149.5")
IRVING_TD = (f"p:{IRVING}", "player_anytime_td", "yes")
DEN_SPREAD = ("t:DEN", "spreads", "home:-2.5")


def play(tmp_path, monkeypatch, scenario, *, week=WEEK, deadline="thu 19:00", **settings):
    """Run a scenario with a fresh world and a clock the test controls.

    Most scenarios use a Thursday-evening deadline so that Thursday's game is in
    play; the Saturday default and Thanksgiving week have scenarios of their own.
    """

    async def run():
        world = await build(tmp_path, week=week, deadline=deadline, **settings)
        clock = {"now": TUE_NOON}

        async def at(moment, *, tick=True):
            clock["now"] = moment
            if tick:
                await world.tick(moment)

        try:
            # The bot loads each cog as a fresh module, so patch the ones it is actually using.
            for cog in ("picks", "backfill", "slip", "admin", "config"):
                monkeypatch.setattr(sys.modules[f"longshot.cogs.{cog}"], "utcnow", lambda: clock["now"])
            await scenario(world, at)
        finally:
            await world.close()

    asyncio.run(run())


def test_a_week_where_the_parlay_hits(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel

        # Tuesday 10:00: the lowest scorer is named and picks open.
        await at(TUE_ANNOUNCE)
        assert len(channel.sent) == 1
        opening = channel.sent[0]
        assert "Week 6 parlay" in opening.content
        assert "**Team 2** had the lowest score" in opening.content and "78.32" in opening.content and "$10" in opening.content
        assert "Picks are open" in opening.content and "DraftKings" in opening.content
        assert opening.view is not None  # the Mark paid button
        debt = await world.bot.db.debt(SEASON, WEEK)
        assert (debt["roster_id"], debt["amount"], debt["paid_at"]) == (2, 10, None)
        await at(TUE_ANNOUNCE)
        assert len(channel.sent) == 1  # a second tick does not announce again

        # Members look up their bets.
        await at(TUE_NOON, tick=False)
        reply = await world.pick(1, *IRVING_150)
        assert "Bucky Irving 150+ rushing yards" in reply and "+320 at DraftKings" in reply
        assert channel.last().startswith("🔒 **Team 1** locked in **Bucky Irving 150+ rushing yards** (+320)")
        assert "1 of 3 picks in" in channel.last()

        await world.pick(2, "t:DEN", "h2h", "home")
        assert "**DEN moneyline** (-140)" in channel.last()
        await world.pick(2, *DEN_SPREAD)
        assert channel.last().startswith("🔁 **Team 2** changed their pick to **DEN -2.5** (-110)")
        assert "2 of 3 picks in" in channel.last()

        # Wednesday evening: 24 hours out, only the member without a pick is nudged.
        channel.clear()
        await at(WED_EVENING)
        assert len(channel.sent) == 1 and "Still waiting on **Team 3**" in channel.last()
        assert list(world.dms) == [3] and "pick is due" in world.dms[3].last()
        await at(WED_EVENING)
        assert len(channel.sent) == 1

        # Thursday 7:00 PM: picks close. Nobody is given a bet.
        channel.clear()
        await at(THU_DEADLINE)
        closing = channel.sent[0]
        assert "Week 6 picks are closed" in closing.content and "No pick from **Team 3**" in closing.content
        assert closing.embed.title == "Week 6 slip" and "2 legs" in closing.embed.fields[0].name
        assert [row["discord_id"] for row in await world.bot.db.no_shows(SEASON)] == [3]
        assert set(await world.statuses()) == {1, 2}
        assert "picks are closed" in await world.pick(3, *IRVING_TD)

        # The commissioner enters a late pick for the no-show.
        reply = await world.pick(3, *IRVING_TD, by=99)
        assert "<@3>'s Week 6 pick is **Bucky Irving anytime TD**" in reply
        assert channel.last().startswith("📝 The commissioner entered a pick for **Team 3**")

        # Thursday night: nothing is settled at half-time.
        channel.clear()
        world.espn.set(TNF, state="in", home=7, away=3)
        await at(THU_HALFTIME)
        assert channel.sent == []
        assert set((await world.statuses()).values()) == {"pending"}

        # Denver wins by 4 and covers 2.5. The slate update follows.
        world.espn.set(TNF, state="post", home=24, away=20)
        await at(THU_LATE)
        assert channel.texts[0] == "✅ **Leg hit** · Team 2 · DEN -2.5 (-110)"
        slate = channel.texts[1]
        assert slate.startswith("### Thursday night slate · Week 6")
        assert "✅ **Team 2** · DEN -2.5 (-110)" in slate
        assert "**Parlay: alive.** 1 of 3 legs in, 2 to go." in slate
        assert "**Next:** Sunday early" in slate and "2 legs: Team 1, Team 3" in slate
        await at(THU_LATE)
        assert len(channel.sent) == 2  # no repeats

        # Sunday, mid-game: a touchdown is already in the books; a yardage leg waits for the whistle.
        channel.clear()
        world.espn.set(BUCS_GAME, state="in", home=14, away=10)
        await at(SUN_EARLY_LIVE)
        assert channel.texts == ["✅ **Leg hit** · Team 3 · Bucky Irving anytime TD (-130)"]
        statuses = await world.statuses()
        assert (statuses[1], statuses[3]) == ("pending", "won")
        assert (await world.bot.db.member_pick(SEASON, WEEK, MAIN, 1))["stat_value"] == 165

        # Final whistle: the last leg lands and the parlay cashes.
        channel.clear()
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert channel.texts[0] == "✅ **Leg hit** · Team 1 · Bucky Irving 150+ rushing yards (+320) · *finished with 165*"
        recap = channel.sent[-1]
        expected = total_return(10, parlay_decimal([320, -110, -130]))
        assert "THE WEEK 6 PARLAY HIT" in recap.content and f"${expected:,.2f}" in recap.content
        assert "estimated" in recap.content  # /placed was never run
        assert len(channel.sent) == 2  # the last slate has no update of its own; the recap covers it
        week = await world.week()
        assert (week["result"], week["payout"], week["killer_pick_id"]) == ("won", expected, None)
        await at(SUN_EARLY_DONE)
        assert len(channel.sent) == 2

        stats = compute(
            await world.bot.db.members(), await world.bot.db.all_picks(SEASON),
            await world.bot.db.parlays(SEASON), await world.bot.db.no_shows(SEASON),
        )
        assert [stats[n].won for n in (1, 2, 3)] == [1, 1, 1]
        assert (stats[2].low_scores, stats[3].no_shows) == (1, 1)

    play(tmp_path, monkeypatch, scenario)


def test_lookups_are_cached_and_cost_what_was_budgeted(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await world.pick(2, "t:DEN", "h2h", "home")
        await world.pick(1, *IRVING_150)
        assert world.odds.calls == [
            "events",  # free
            "event:player_rush_yds,player_rush_yds_alternate",  # 2 credits
            "main",  # 3 credits, covers every game's moneyline, spread and total
            "event:alternate_spreads",  # 1 credit
        ]
        assert world.odds.remaining == 480 - 6
        assert await world.bot.linebook.usage() == (474, 26)

    play(tmp_path, monkeypatch, scenario)


def test_a_missed_leg_kills_the_parlay(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await world.pick(3, *IRVING_TD)
        await at(THU_DEADLINE)
        assert "No pick from" not in channel.last()

        channel.clear()
        world.espn.set(TNF, state="post", home=21, away=20)  # Denver wins by 1: does not cover 2.5
        await at(THU_LATE)
        # The miss and the death are announced. There is no slate update for a dead parlay.
        assert channel.texts == [
            "❌ **Leg missed** · Team 2 · DEN -2.5 (-110)",
            "💀 **The Week 6 parlay is dead.** Team 2's leg missed: DEN -2.5 (-110).",
        ]
        killer = (await world.week())["killer_pick_id"]
        assert (await world.bot.db.pick(killer))["discord_id"] == 2

        # While games remain, the channel hears nothing more: not the other legs, not the slates.
        channel.clear()
        world.espn.set(BUCS_GAME, state="in", home=14, away=10)
        await at(SUN_EARLY_LIVE)
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert channel.sent == []
        # The other legs were still graded, silently, though every leg is now settled.
        assert await world.statuses() == {1: "won", 2: "lost", 3: "won"}
        assert (await world.week())["recap_at"] is None

        # Monday night's game is the last in the window. Nothing until it ends...
        last = "WSH @ SF"
        for label in world.espn.games:
            if label != last:
                world.espn.set(label, state="post", home=20, away=17)
        world.espn.set(last, state="in", home=3, away=0)
        await at(et(2026, 10, 19, 21, 0))
        assert channel.sent == []

        # ...then one final update with every leg's status.
        world.espn.set(last, state="post", home=23, away=20)
        await at(et(2026, 10, 19, 23, 30))
        assert len(channel.sent) == 1
        final = channel.sent[0]
        assert final.content == (
            "**Week 6 parlay: final.** 2 of 3 legs hit. It would have cashed without Team 2's leg."
        )
        assert final.embed.title == "Week 6 final slip"
        assert final.embed.description.splitlines() == [
            "🟥🟩🟩  2 of 3 legs in",
            "",
            "❌ **Team 2** · DEN -2.5 (-110)",
            "└ DEN won by 1 · did not cover",
            "✅ **Team 1** · Bucky Irving 150+ rushing yards (+320)",
            "└ ▰▰▰▰▰▰▰▰▰▰  165 / 150",
            "✅ **Team 3** · Bucky Irving anytime TD (-130)",
            "└ scored",
        ]
        week = await world.week()
        assert (week["result"], week["payout"], bool(week["recap_at"])) == ("lost", None, True)
        await at(et(2026, 10, 20, 9, 0))
        assert len(channel.sent) == 1  # it is posted once

    play(tmp_path, monkeypatch, scenario)


def test_a_second_miss_on_a_dead_parlay_is_silent(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, f"p:{IRVING}", "player_rush_yds", "over:174.5")  # 175+; he finishes with 165
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)

        world.espn.set(TNF, state="post", home=21, away=20)
        await at(THU_LATE)
        channel.clear()
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert channel.sent == []
        assert await world.statuses() == {1: "lost", 2: "lost"}
        # The first miss stays on record as the one that killed it.
        killer = (await world.week())["killer_pick_id"]
        assert (await world.bot.db.pick(killer))["discord_id"] == 2

        world.espn.finish_everything()
        await at(et(2026, 10, 19, 23, 30))
        assert channel.texts == ["**Week 6 parlay: final.** 0 of 2 legs hit. 2 legs missed."]

    play(tmp_path, monkeypatch, scenario)


def test_overturning_the_miss_brings_the_parlay_back(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel, db = world.channel, world.bot.db
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)
        world.espn.set(TNF, state="post", home=21, away=20)
        await at(THU_LATE)
        killer = (await world.week())["killer_pick_id"]

        # What /grade does when the commissioner rules the leg a winner after all.
        channel.clear()
        await db.grade_pick(killer, "won", None, "Settled by the commissioner", "99")
        await tracking.announce_grades(world.bot, SEASON, WEEK, MAIN, [killer])
        assert channel.texts == [
            "✅ **Leg hit** · Team 2 · DEN -2.5 (-110)\n🟢 **The Week 6 parlay is alive again.**"
        ]
        assert (await world.week())["killer_pick_id"] is None

        # Updates resume, and it can still cash.
        channel.clear()
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert channel.texts[0].startswith("✅ **Leg hit** · Team 1")
        assert "THE WEEK 6 PARLAY HIT" in channel.last()

    play(tmp_path, monkeypatch, scenario)


def test_a_player_who_sits_out_voids_the_leg(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        reply = await world.pick(1, f"p:{BENCHED}", "player_rush_yds", "over:20.5")
        assert "Benny Benchwarmer over 20.5 rushing yards" in reply
        await at(THU_DEADLINE)
        world.channel.clear()
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        assert "➖ **Leg dropped**" in world.channel.texts[0] and "Did not play" in world.channel.texts[0]
        assert "Week 6 parlay: void" in world.channel.last()
        assert (await world.week())["result"] == "void"

    play(tmp_path, monkeypatch, scenario)


def test_picks_that_are_refused(tmp_path, monkeypatch):
    async def scenario(world, at):
        # Before the announcement there is nothing to pick.
        assert "not open" in await world.pick(1, *IRVING_150)

        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        before = len(world.channel.sent)

        assert "already has that exact leg" in await world.pick(3, *IRVING_150)
        assert "direct opposite of **Team 2**'s leg" in await world.pick(3, "t:SEA", "spreads", "away:2.5")
        assert "not linked" in await world.pick(7, *IRVING_150)
        assert "could not find that player or team" in await world.pick(3, "p:000", "player_rush_yds", "over:1.5")
        assert "Choose the bet" in await world.pick(3, f"p:{IRVING}", "h2h", "home")
        assert "has no **Receptions** line for **Bucky Irving**" in await world.pick(3, f"p:{IRVING}", "player_receptions", "over:2.5")
        assert "not on the list" in await world.pick(3, f"p:{IRVING}", "player_rush_yds", "over:60.5")
        assert "not on the list" in await world.pick(3, f"p:{IRVING}", "player_rush_yds", "ove")

        # None of that saved or announced anything.
        assert set(await world.statuses()) == {1, 2}
        assert len(world.channel.sent) == before

        # A different line on the same player is fine, and names can be typed in full.
        reply = await world.pick(3, "Bucky Irving", "Rushing yards", "175+")
        assert "Bucky Irving 175+ rushing yards" in reply and "+900" in reply

    play(tmp_path, monkeypatch, scenario)


def test_autocomplete_lists(tmp_path, monkeypatch):
    async def scenario(world, at):
        picks = world.picks
        names = lambda choices: [choice.name for choice in choices]

        assert names(await picks._who_choices("", late=False)) == ["Picks are not open right now."]
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)

        teams = await picks._who_choices("", late=False)
        assert len(teams) == 25  # Discord's limit; typing narrows it
        assert teams[0].name == "Seattle Seahawks · SEA @ DEN Thu 8:15 PM" and teams[0].value == "t:SEA"

        found = await picks._who_choices("irv", late=False)
        assert [(c.name, c.value) for c in found] == [("Bucky Irving · RB · PIT @ TB Sun 1:00 PM", f"p:{IRVING}")]
        assert names(await picks._who_choices("den", late=False))[0].startswith("Denver Broncos")

        for_player = await picks._bet_choices(FakeInteraction(world.bot, 1, who=f"p:{IRVING}"), "", late=False)
        assert names(for_player)[:3] == ["Rushing yards", "Rushing attempts", "Receptions"]
        for_passer = await picks._bet_choices(FakeInteraction(world.bot, 1, who=f"p:{DANIELS}"), "", late=False)
        assert names(for_passer)[0] == "Passing yards"
        for_team = await picks._bet_choices(FakeInteraction(world.bot, 1, who="t:DEN"), "", late=False)
        assert [(c.name, c.value) for c in for_team] == [("Moneyline", "h2h"), ("Spread", "spreads"), ("Game total", "totals")]
        assert names(await picks._bet_choices(FakeInteraction(world.bot, 1, who=""), "", late=False)) == [
            "Choose a player or team first."
        ]

        rushing = FakeInteraction(world.bot, 1, who=f"p:{IRVING}", bet="player_rush_yds")
        assert names(await picks._line_choices(rushing, "", late=False)) == [
            "Over 74.5 (-115)", "Under 74.5 (-105)", "150+ (+320)", "175+ (+900)",
        ]
        narrowed = await picks._line_choices(rushing, "150", late=False)
        assert [(c.name, c.value) for c in narrowed] == [("150+ (+320)", "over:149.5")]

        spread = FakeInteraction(world.bot, 1, who="t:DEN", bet="spreads")
        assert names(await picks._line_choices(spread, "", late=False)) == ["DEN -2.5 (-110)", "DEN -6.5 (+150)"]
        total = FakeInteraction(world.bot, 1, who="t:SEA", bet="totals")
        assert names(await picks._line_choices(total, "", late=False)) == [
            "Over 43.5 (-110)", "Under 43.5 (-110)", "Over 47.5 (+140)", "Under 47.5 (-170)",
        ]

        receptions = FakeInteraction(world.bot, 1, who=f"p:{IRVING}", bet="player_receptions")
        assert names(await picks._line_choices(receptions, "", late=False)) == [
            "DraftKings has no line for this bet right now."
        ]
        world.odds.fail = True
        passing = FakeInteraction(world.bot, 1, who=f"p:{DANIELS}", bet="player_pass_yds")
        assert names(await picks._line_choices(passing, "", late=False)) == [
            "The odds feed did not respond. Try again in a moment."
        ]

    play(tmp_path, monkeypatch, scenario)


def test_a_sunday_deadline_leaves_out_earlier_games(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        names = [choice.name for choice in await world.picks._who_choices("", late=False)]
        assert not any("SEA @ DEN" in name for name in names)  # Thursday night
        assert not any("HOU @ JAX" in name for name in names)  # Sunday 9:30 AM in London
        assert any("PIT @ TB" in name for name in names)
        assert "could not find that player or team" in await world.pick(2, *DEN_SPREAD)

        # Thursday's default deadline passes without closing anything.
        await at(THU_DEADLINE)
        assert (await world.week())["closed_at"] is None
        await at(et(2026, 10, 18, 12, 1))
        assert (await world.week())["closed_at"] is not None

    play(tmp_path, monkeypatch, scenario, deadline="sun 12:00")


def test_starting_the_bot_after_the_deadline_skips_that_week(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(et(2026, 10, 16, 12, 0))  # first ever run, on Friday
        assert len(world.channel.sent) == 1
        assert "Team 2" in world.channel.last() and "deadline has already passed" in world.channel.last()
        week = await world.week()
        assert week["closed_at"] and week["skipped"] == 1
        assert await world.bot.db.no_shows(SEASON) == []  # nobody is blamed for a week that never opened

        # With no picks there is nothing to track, and the week is quietly closed out.
        world.espn.finish_everything()
        await at(et(2026, 10, 20, 9, 0))
        assert len(world.channel.sent) == 1
        assert (await world.week())["result"] == "void"

    play(tmp_path, monkeypatch, scenario)


def test_a_tie_for_lowest_score_is_left_to_the_commissioner(tmp_path, monkeypatch):
    async def scenario(world, at):
        world.sleeper.scores = {1: 80.0, 2: 80.0, 3: 99.0}
        await at(TUE_ANNOUNCE)
        text = world.channel.last()
        assert "tie at **80** points" in text and "Team 1" in text and "Team 2" in text and "/admin loser" in text
        assert await world.bot.db.debt(SEASON, WEEK) is None
        assert json.loads((await world.week())["tied_roster_ids"]) == [1, 2]
        assert "Picks are open" in text  # the tie does not hold up the week

    play(tmp_path, monkeypatch, scenario)


def test_low_credits_stop_new_lookups_but_not_cached_ones(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        world.odds.remaining = 52
        # The 3-credit main lookup leaves 49, under the reserve of 50. The alternates
        # lookup that would follow is not made, and the pick goes through on the main line.
        assert "DEN -2.5" in await world.pick(2, *DEN_SPREAD)
        assert world.odds.remaining == 49
        assert "**49**" in world.dms[99].last() and "credits left" in world.dms[99].last()

        calls = list(world.odds.calls)
        with pytest.raises(NoCredits):
            await world.pick(1, *IRVING_150)
        blocked = FakeInteraction(world.bot, 1, who=f"p:{IRVING}", bet="player_rush_yds")
        choices = await world.picks._line_choices(blocked, "", late=False)
        assert choices[0].name == "The odds feed is out of credits for now. Ask the commissioner."
        assert world.odds.calls == calls  # nothing more was spent

        # A list already fetched still works.
        assert "DEN moneyline" in await world.pick(3, "t:DEN", "h2h", "home")
        assert len(world.dms[99].sent) == 1  # the commissioner is told once, not on every lookup

    play(tmp_path, monkeypatch, scenario)


# --- Early Exit ---------------------------------------------------------------------
# Bucky Irving ran for 165 yards, so 175+ misses and under 74.5 misses.

IRVING_175 = (f"p:{IRVING}", "player_rush_yds", "over:174.5")
HURT = InjuryNote("TB", "B.Irving", False, 3)
BACK = InjuryNote("TB", "B.Irving", True, 4)


async def rule_injury(world, member: int, ruling: str) -> str:
    """What /injury does when the commissioner rules on a member's leg."""
    cog = world.bot.get_cog("Slip")
    interaction = FakeInteraction(world.bot, 99)
    await cog.injury.callback(cog, interaction, user(member), app_commands.Choice(name=ruling, value=ruling))
    return interaction.reply


def injury_week(tmp_path, monkeypatch, check, *, leg=IRVING_175, notes=(), ruling=None):
    """Member 1 picks an Irving leg, member 2 the Denver spread; the Buccaneers game ends with these injury notes."""

    async def scenario(world, at):
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *leg)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)
        if ruling:
            await rule_injury(world, 1, ruling)
        world.channel.clear()
        world.espn.injuries = list(notes)
        world.espn.set(BUCS_GAME, state="post", home=24, away=16)
        await at(SUN_EARLY_DONE)
        await check(world, at)

    play(tmp_path, monkeypatch, scenario)


def test_an_early_exit_voids_a_miss(tmp_path, monkeypatch):
    async def check(world, at):
        assert "➖ **Leg dropped**" in world.channel.texts[0]
        assert "Early Exit: left injured and did not return" in world.channel.texts[0]
        assert not any("dead" in text for text in world.channel.texts)
        assert (await world.statuses())[1] == "void"

    injury_week(tmp_path, monkeypatch, check, notes=[HURT])


def test_an_under_is_not_voided_by_an_early_exit(tmp_path, monkeypatch):
    async def check(world, at):
        assert (await world.statuses())[1] == "lost"
        assert any("parlay is dead" in text for text in world.channel.texts)

    injury_week(tmp_path, monkeypatch, check, leg=(f"p:{IRVING}", "player_rush_yds", "under:74.5"), notes=[HURT])


def test_a_miss_with_no_injury_counts_at_once(tmp_path, monkeypatch):
    async def check(world, at):
        assert (await world.statuses())[1] == "lost"

    injury_week(tmp_path, monkeypatch, check)


def test_a_player_who_came_back_is_not_protected(tmp_path, monkeypatch):
    async def check(world, at):
        assert (await world.statuses())[1] == "lost"
        assert any("parlay is dead" in text for text in world.channel.texts)

    injury_week(tmp_path, monkeypatch, check, notes=[HURT, BACK])


def test_the_commissioner_rules_an_early_exit_espn_missed(tmp_path, monkeypatch):
    async def check(world, at):
        world.channel.clear()
        reply = await rule_injury(world, 1, INJURED)
        assert "is **void**: an Early Exit" in reply
        assert (await world.statuses())[1] == "void"
        text = world.channel.texts[0]
        assert "Leg dropped" in text and "Early Exit" in text and "alive again" in text

    injury_week(tmp_path, monkeypatch, check, notes=[HURT, BACK])


def test_the_commissioner_overrules_espn(tmp_path, monkeypatch):
    async def check(world, at):
        assert (await world.statuses())[1] == "void"
        world.channel.clear()
        reply = await rule_injury(world, 1, NOT_INJURED)
        assert "counts as a **miss**" in reply
        assert (await world.statuses())[1] == "lost"
        assert "parlay is dead" in world.channel.last()
        # Back to ESPN: void again, and the parlay is alive.
        await rule_injury(world, 1, "espn")
        assert (await world.statuses())[1] == "void"
        assert "alive again" in world.channel.last()

    injury_week(tmp_path, monkeypatch, check, notes=[HURT])


def test_ruling_an_injury_before_the_final_whistle(tmp_path, monkeypatch):
    async def check(world, at):
        # ESPN logged nothing, but the commissioner had already ruled.
        assert (await world.statuses())[1] == "void"

    injury_week(tmp_path, monkeypatch, check, ruling=INJURED)


def test_injury_rulings_that_change_nothing(tmp_path, monkeypatch):
    async def check(world, at):
        assert "team bet" in await rule_injury(world, 2, INJURED)
        assert (await world.statuses())[1] == "won"
        assert "hit, so it stands" in await rule_injury(world, 1, INJURED)
        assert (await world.statuses())[1] == "won"

    injury_week(tmp_path, monkeypatch, check, leg=IRVING_150, notes=[HURT])
