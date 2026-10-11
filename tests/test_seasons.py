"""Looking back at an earlier season with the `season` option."""

from test_week_flow import DEN_SPREAD, IRVING_150, THU_DEADLINE, TUE_ANNOUNCE, TUE_NOON, play
from world import SEASON, WEEK

from longshot import seasons
from longshot.db import iso, utcnow
from longshot.domain.cards import MAIN

LAST_SEASON = SEASON - 1


def last_year_pick(week, discord_id, status):
    return dict(
        season=LAST_SEASON, week=week, card=MAIN, discord_id=discord_id, espn_event_id=f"old-{week}",
        game="PHI @ DAL", kickoff="2025-10-05T17:00:00+00:00", home_abbr="DAL", away_abbr="PHI", market="h2h",
        side="away", line=None, price=-150, alt=0, team_abbr="PHI", player_id=None, player_name=None,
        label=f"PHI moneyline (member {discord_id})", entered_by=discord_id, status=status,
    )


async def add_last_season(db):
    """Two finished weeks from the year before: one parlay hit, one was killed by member 2."""
    stamp = iso(utcnow())
    for week, results in ((4, {1: "won", 2: "won"}), (5, {1: "won", 2: "lost"})):
        await db.ensure_parlay(LAST_SEASON, week, MAIN)
        killer = None
        for discord_id, status in results.items():
            pick_id, _ = await db.save_pick(last_year_pick(week, discord_id, status))
            await db.grade_pick(pick_id, status, None, None, "auto")
            killer = pick_id if status == "lost" else killer
        await db.update_parlay(
            LAST_SEASON, week, MAIN, stake=10, announced_at=stamp, closed_at=stamp, recap_at=stamp,
            result="lost" if killer else "won", payout=None if killer else 22.5, placed_at=stamp,
            killer_pick_id=killer, loser_roster_id=3, loser_discord_id=3, loser_team="Team 3",
        )
        await db.set_debt(LAST_SEASON, week, 3, "Team 3", 10, discord_id=3)
    await db.mark_paid(LAST_SEASON, 4)


def test_looking_back_at_an_earlier_season(tmp_path, monkeypatch):
    async def scenario(world, at):
        bot, db = world.bot, world.bot.db
        stats_cog, slip_cog = bot.get_cog("Stats"), bot.get_cog("Slip")
        await add_last_season(db)

        # This season is under way too.
        await at(TUE_ANNOUNCE)
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        await at(THU_DEADLINE)

        # The season list offers every year with parlays on record, newest first.
        assert [(c.name, c.value) for c in await seasons.choices(bot, "")] == [("2026", 2026), ("2025", 2025)]
        assert [c.value for c in await seasons.choices(bot, "2025")] == [2025]
        assert await seasons.resolve(bot, None) == SEASON and await seasons.resolve(bot, 2025) == 2025

        # Stats: with no season, this year; with one, only that year.
        season, now, _, parlays = await stats_cog._season()
        assert season == SEASON and len(parlays) == 1
        assert (now[1].legs, now[1].won, now[3].low_scores) == (1, 0, 0)

        season, then, names, parlays = await stats_cog._season(LAST_SEASON)
        assert season == LAST_SEASON and [row["week"] for row in parlays] == [4, 5]
        assert (then[1].won, then[1].lost, then[1].streak) == (2, 0, 2)
        assert (then[2].won, then[2].lost, then[2].kills) == (1, 1, 1)
        assert (then[3].legs, then[3].low_scores) == (0, 2)
        assert names[3] == "Team 3"

        _, empty, _, parlays = await stats_cog._season(2019)
        assert parlays == [] and all(entry.legs == 0 for entry in empty.values())

        # Slips: an earlier season with no week shows its last parlay; a week picks one out.
        assert [(r["season"], r["week"]) for r in await slip_cog._parlays(None, season=LAST_SEASON)] == [(2025, 5)]
        assert [(r["season"], r["week"]) for r in await slip_cog._parlays(4, season=LAST_SEASON)] == [(2025, 4)]
        assert await slip_cog._parlays(9, season=LAST_SEASON) == []
        assert await slip_cog._parlays(None, season=2019) == []
        # With no season it is still the live week, which is all /placed and /grade ever act on.
        assert [(r["season"], r["week"]) for r in await slip_cog._parlays(None)] == [(SEASON, WEEK)]
        assert [(r["season"], r["week"]) for r in await slip_cog._parlays(WEEK, season=SEASON)] == [(SEASON, WEEK)]

        # The ledger keeps each season's debts apart.
        assert [(d["week"], bool(d["paid_at"])) for d in await db.debts(LAST_SEASON)] == [(4, True), (5, False)]
        assert [d["week"] for d in await db.debts(SEASON)] == [WEEK]

    play(tmp_path, monkeypatch, scenario)


def test_season_option_is_offered_on_the_look_back_commands(tmp_path, monkeypatch):
    async def scenario(world, at):
        tree = world.bot.tree
        for name in ("slip", "stats", "leaderboard", "ledger"):
            options = {option["name"]: option for option in tree.get_command(name).to_dict(tree)["options"]}
            season = options["season"]
            assert (season["required"], season["autocomplete"]) == (False, True), name
            assert (season["min_value"], season["max_value"]) == (2000, 2100), name
        # Commands that change things never take a season: they act on the live week only.
        for name in ("placed", "grade", "pick", "pickfor", "paid"):
            options = {option["name"] for option in tree.get_command(name).to_dict(tree).get("options", [])}
            assert "season" not in options, name

    play(tmp_path, monkeypatch, scenario)
