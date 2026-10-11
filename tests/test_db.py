import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from longshot.db import Database


def run(coroutine):
    return asyncio.run(coroutine)


async def fresh() -> Database:
    database = Database(":memory:")
    await database.connect()
    return database


def pick(discord_id=1, **overrides):
    base = dict(
        season=2026, week=6, discord_id=discord_id, espn_event_id="401", game="PHI @ JAX",
        kickoff="2026-10-18T17:00:00+00:00", home_abbr="JAX", away_abbr="PHI", market="player_rush_yds",
        side="over", line=39.5, price=105, alt=1, team_abbr=None, player_id="4040715",
        player_name="Jalen Hurts", label="Jalen Hurts 40+ rushing yards", entered_by=discord_id,
    )
    return base | overrides


def test_settings_state_and_cache():
    async def scenario():
        db = await fresh()
        assert await db.get_setting("deadline") is None
        await db.set_setting("deadline", "thu 19:00")
        await db.set_setting("deadline", "sun 12:00")
        assert await db.get_setting("deadline") == "sun 12:00"
        assert await db.get_state("league_id", "none") == "none"
        await db.set_state("league_id", 12345)
        assert await db.get_state("league_id") == "12345"
        assert await db.cache_get("events") is None
        await db.cache_put("events", [{"id": "a"}])
        payload, fetched_at = await db.cache_get("events")
        assert payload == [{"id": "a"}]
        assert datetime.now(timezone.utc) - fetched_at < timedelta(seconds=5)
        await db.close()

    run(scenario())


def test_linking_keeps_one_team_per_person():
    async def scenario():
        db = await fresh()
        await db.link_member(1, 100, "Team One", "u1")
        await db.link_member(2, 200, "Team Two", "u2")
        await db.link_member(2, 100, "Team Two", "u2")  # person 100 moves to team 2
        members = await db.members()
        assert [(m["roster_id"], m["discord_id"]) for m in members] == [(2, 100)]
        assert (await db.member(100))["team_name"] == "Team Two"
        assert await db.member_by_roster(1) is None
        assert await db.unlink_member(100) is True
        assert await db.unlink_member(100) is False
        await db.close()

    run(scenario())


def test_a_second_pick_replaces_the_first():
    async def scenario():
        db = await fresh()
        first_id, replaced = await db.save_pick(pick())
        assert replaced is False
        await db.grade_pick(first_id, "won", 52, None, "auto")
        second_id, replaced = await db.save_pick(pick(label="PHI moneyline", market="h2h", side="away", line=None))
        assert replaced is True
        picks = await db.week_picks(2026, 6)
        assert len(picks) == 1
        # The replacement starts ungraded.
        assert (picks[0]["id"], picks[0]["label"], picks[0]["status"]) == (second_id, "PHI moneyline", "pending")
        await db.close()

    run(scenario())


def test_started_pending_picks():
    async def scenario():
        db = await fresh()
        early, _ = await db.save_pick(pick(1, kickoff="2026-10-16T00:15:00+00:00"))
        await db.save_pick(pick(2, kickoff="2026-10-18T17:00:00+00:00"))
        graded, _ = await db.save_pick(pick(3, kickoff="2026-10-16T00:15:00+00:00"))
        await db.grade_pick(graded, "lost", 12, None, "auto")
        now = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
        assert [p["id"] for p in await db.started_pending_picks(now)] == [early]
        await db.close()

    run(scenario())


def test_week_updates_reject_unknown_columns():
    async def scenario():
        db = await fresh()
        week = await db.ensure_week(2026, 6)
        assert week["announced_at"] is None and week["reminders_sent"] == "[]"
        await db.update_week(2026, 6, stake=10, placed_odds=125000)
        assert (await db.week(2026, 6))["placed_odds"] == 125000
        with pytest.raises(ValueError):
            await db.update_week(2026, 6, nonsense=1)
        await db.close()

    run(scenario())


def test_debt_and_payment():
    async def scenario():
        db = await fresh()
        await db.set_debt(2026, 6, roster_id=3, team_name="Team Three", amount=10)
        assert (await db.debt(2026, 6))["paid_at"] is None
        assert await db.mark_paid(2026, 6) is True
        assert await db.mark_paid(2026, 6) is False  # already settled
        assert (await db.debt(2026, 6))["paid_at"] is not None
        assert await db.mark_paid(2026, 7) is False  # no such debt

        # Announcing the same loser again changes nothing; a corrected loser starts over unpaid.
        await db.set_debt(2026, 6, roster_id=3, team_name="Team Three", amount=10)
        assert (await db.debt(2026, 6))["paid_at"] is not None
        await db.set_debt(2026, 6, roster_id=4, team_name="Team Four", amount=10)
        debt = await db.debt(2026, 6)
        assert (debt["roster_id"], debt["paid_at"]) == (4, None)
        assert len(await db.debts(2026)) == 1
        await db.close()

    run(scenario())
