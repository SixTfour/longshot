"""The default Saturday deadline, and Thanksgiving week with its two parlays, played out against world.py."""

from test_week_flow import play
from world import IRVING, MONDAY_BUCS, SEASON, THANKSGIVING_WEEK, TNF, TURKEY_GAME, et

from longshot.domain.cards import BONUS, MAIN
from longshot.domain.odds_math import parlay_decimal, total_return
from longshot.domain.stats import compute

IRVING_150 = (f"p:{IRVING}", "player_rush_yds", "over:149.5")
IRVING_TD = (f"p:{IRVING}", "player_anytime_td", "yes")
HOME_SPREAD = ("spreads", "home:-2.5")


def names(choices):
    return [choice.name for choice in choices]


def test_the_default_deadline_is_saturday_night(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel
        await at(et(2026, 10, 13, 10, 1))
        assert "Picks are open" in channel.last()

        await at(et(2026, 10, 13, 12, 0), tick=False)
        listed = names(await world.picks._who_choices("", late=False))
        assert not any("SEA @ DEN" in name for name in listed)  # Thursday night is before the deadline
        assert listed[0] == "Team HOU · HOU @ JAX Sun 9:30 AM"
        assert any("PIT @ TB Sun 1:00 PM" in name for name in listed)
        assert names(await world.picks._who_choices("sf", late=False)) == ["Team SF · WSH @ SF Mon 8:15 PM"]
        assert "could not find that player or team" in await world.pick(2, "t:DEN", *HOME_SPREAD)
        assert "Your Week 6 pick is **Bucky Irving 150+ rushing yards**" in await world.pick(1, *IRVING_150)

        # Thursday's game comes and goes without closing anything.
        channel.clear()
        world.espn.set(TNF, state="post", home=24, away=20)
        await at(et(2026, 10, 15, 23, 45))
        assert channel.sent == [] and (await world.week())["closed_at"] is None

        # Friday 9 PM: the 24-hour reminder. Saturday 9 PM: picks close.
        await at(et(2026, 10, 16, 21, 1))
        assert channel.last().startswith("⏰ Week 6 picks close") and "<@2> <@3>" in channel.last()
        await at(et(2026, 10, 17, 20, 59))
        assert (await world.week())["closed_at"] is None
        await at(et(2026, 10, 17, 21, 1))
        assert "Week 6 picks are closed" in channel.last()
        assert (await world.week())["closed_at"] is not None

    play(tmp_path, monkeypatch, scenario, deadline="sat 21:00")


def test_thanksgiving_week_runs_a_bonus_parlay_and_the_weekly_one(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel, db = world.channel, world.bot.db

        # Tuesday 10:00: one announcement covers both parlays.
        await at(et(2026, 11, 24, 10, 1))
        assert len(channel.sent) == 1
        text = channel.last()
        assert "## Week 12 parlays" in text and "**Two parlays this week**" in text
        assert "**Team 2** (<@2>)" in text and "owes **$10** for the Sunday and Monday parlay" in text
        assert (
            "- **Thanksgiving bonus**: Wednesday, Thursday and Friday games, paid for by the commissioner. Picks close"
            in text
        )
        assert "- **Week 12**: Sunday and Monday games, paid for by **Team 2**. Picks close" in text
        assert [row["card"] for row in await db.parlays(SEASON, THANKSGIVING_WEEK)] == [BONUS, MAIN]
        assert all(row["announced_at"] and not row["closed_at"] for row in await db.parlays(SEASON))
        # The lowest scorer owes one stake, for the weekly parlay. The bonus creates no debt.
        debts = await db.debts(SEASON)
        assert [(debt["week"], debt["roster_id"], debt["amount"]) for debt in debts] == [(THANKSGIVING_WEEK, 2, 10)]

        # Both parlays take picks from Tuesday. The game chosen decides which parlay a pick joins.
        await at(et(2026, 11, 24, 12, 0), tick=False)
        picks = world.picks
        assert names(await picks._who_choices("det", late=False))[0] == "Detroit Lions · CHI @ DET Thu 1:00 PM"
        assert names(await picks._who_choices("irv", late=False)) == ["Bucky Irving · RB · CAR @ TB Mon 8:15 PM"]

        assert "Your Thanksgiving bonus pick is **DET -2.5**" in await world.pick(1, "t:DET", *HOME_SPREAD)
        assert channel.last().endswith("Thanksgiving bonus: 1 of 3 picks in")
        assert "Your Week 12 pick is **Bucky Irving 150+ rushing yards**" in await world.pick(1, *IRVING_150)
        assert channel.last().endswith("Week 12: 1 of 3 picks in")
        await world.pick(2, "t:DET", "h2h", "home")
        assert set(await world.statuses(BONUS)) == {1, 2} and set(await world.statuses(MAIN)) == {1}

        # Tuesday 7:00 PM: two hours before the bonus deadline. Only the bonus parlay is chased.
        channel.clear()
        await at(et(2026, 11, 24, 19, 1))
        assert len(channel.sent) == 1
        assert channel.last().startswith("⏰ Thanksgiving bonus picks close") and "waiting on <@3>" in channel.last()

        # Tuesday 9:00 PM: the bonus parlay closes; the weekly one stays open.
        channel.clear()
        await at(et(2026, 11, 24, 21, 1))
        assert len(channel.sent) == 1
        closing = channel.sent[0]
        assert "Thanksgiving bonus picks are closed" in closing.content and "No pick from <@3>" in closing.content
        assert closing.embed.title == "Thanksgiving bonus slip"
        assert (await world.parlay(BONUS))["closed_at"] and (await world.parlay(MAIN))["closed_at"] is None
        assert "could not find that player or team" in await world.pick(3, "t:CHI", "h2h", "away")
        assert "Your Week 12 pick is **Bucky Irving anytime TD**" in await world.pick(2, *IRVING_TD)

        # Thanksgiving: Detroit wins by 7. Both bonus legs hit, so the bonus parlay cashes.
        channel.clear()
        world.espn.set(TURKEY_GAME, state="post", home=27, away=20)
        await at(et(2026, 11, 26, 16, 30))
        assert "✅ **Leg hit** · <@1> · DET -2.5 (-110)" in channel.texts[0]
        assert "✅ **Leg hit** · <@2> · DET moneyline (-140)" in channel.texts[0]
        recap = channel.sent[-1]
        bonus_pays = total_return(10, parlay_decimal([-110, -140]))
        assert "THE THANKSGIVING BONUS PARLAY HIT" in recap.content and f"${bonus_pays:,.2f}" in recap.content
        assert recap.embed.title == "Thanksgiving bonus final slip"
        assert len(channel.sent) == 2
        bonus, main = await world.parlay(BONUS), await world.parlay(MAIN)
        assert (bonus["result"], bonus["payout"]) == ("won", bonus_pays)
        assert (main["result"], main["closed_at"]) == (None, None)  # the weekly parlay is untouched

        # Friday and Saturday 9:00 PM: the weekly parlay's own reminder and deadline.
        channel.clear()
        await at(et(2026, 11, 27, 21, 1))
        assert channel.last().startswith("⏰ Week 12 picks close") and "waiting on <@3>" in channel.last()
        await at(et(2026, 11, 28, 21, 1))
        closing = channel.sent[-1]
        assert "Week 12 picks are closed" in closing.content and closing.embed.title == "Week 12 slip"

        # Monday night: the weekly parlay's legs land.
        channel.clear()
        world.espn.set(MONDAY_BUCS, state="post", home=24, away=16)
        await at(et(2026, 12, 1, 0, 5))
        assert "THE WEEK 12 PARLAY HIT" in channel.last()
        assert all(row["result"] == "won" and row["recap_at"] for row in await db.parlays(SEASON, THANKSGIVING_WEEK))
        await at(et(2026, 12, 1, 0, 6))
        assert len(channel.sent) == 2  # legs, then the recap; nothing repeats

        # Each parlay counts on its own: two legs each for members 1 and 2, two missed deadlines for member 3.
        stats = compute(await db.members(), await db.all_picks(SEASON), await db.parlays(SEASON), await db.no_shows(SEASON))
        assert [(stats[n].legs, stats[n].won) for n in (1, 2, 3)] == [(2, 2), (2, 2), (0, 0)]
        assert (stats[3].no_shows, stats[2].low_scores) == (2, 1)

        slip = world.bot.get_cog("Slip")
        assert [row["card"] for row in await slip._parlays(None)] == [BONUS, MAIN]
        assert [row["card"] for row in await slip._parlays(None, BONUS)] == [BONUS]

    play(tmp_path, monkeypatch, scenario, week=THANKSGIVING_WEEK, deadline="sat 21:00")


def test_starting_mid_thanksgiving_week_skips_only_the_bonus_parlay(tmp_path, monkeypatch):
    async def scenario(world, at):
        await at(et(2026, 11, 25, 9, 0))  # first run on Wednesday morning, after the bonus deadline
        text = world.channel.last()
        assert "The Thanksgiving bonus pick deadline has already passed" in text
        assert "- **Week 12**: Sunday and Monday games" in text and "lock it in with `/pick`" in text
        bonus, main = await world.parlay(BONUS), await world.parlay(MAIN)
        assert (bonus["skipped"], bool(bonus["closed_at"])) == (1, True)
        assert (main["skipped"], main["closed_at"]) == (0, None)
        assert await world.bot.db.no_shows(SEASON) == []

        await at(et(2026, 11, 25, 12, 0), tick=False)
        assert "could not find that player or team" in await world.pick(1, "t:DET", *HOME_SPREAD)
        assert "Your Week 12 pick is" in await world.pick(1, *IRVING_150)

    play(tmp_path, monkeypatch, scenario, week=THANKSGIVING_WEEK, deadline="sat 21:00")
