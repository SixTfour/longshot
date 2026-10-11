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
        season=2026, week=6, card="main", discord_id=discord_id, espn_event_id="401", game="PHI @ JAX",
        kickoff="2026-10-18T17:00:00+00:00", home_abbr="JAX", away_abbr="PHI", market="player_rush_yds",
        side="over", line=39.5, price=105, alt=1, team_abbr=None, player_id="4040715",
        player_name="Jalen Hurts", label="Jalen Hurts 40+ rushing yards", entered_by=discord_id,
    )
    return base | overrides


def test_a_database_from_an_earlier_version_gains_the_new_columns(tmp_path):
    async def scenario():
        path = tmp_path / "old.db"
        # A database as an earlier version made it: a parlay table with fewer columns, and a row in it.
        import sqlite3

        old = sqlite3.connect(path)
        old.executescript(
            """
            CREATE TABLE parlays (
                season INTEGER NOT NULL, week INTEGER NOT NULL, card TEXT NOT NULL DEFAULT 'main',
                stake REAL, announced_at TEXT, reminders_sent TEXT NOT NULL DEFAULT '[]', closed_at TEXT,
                PRIMARY KEY (season, week, card)
            );
            INSERT INTO parlays (season, week, stake) VALUES (2026, 6, 10);
            """
        )
        old.commit()
        old.close()

        db = Database(path)
        await db.connect()
        row = await db.parlay(2026, 6, "main")
        assert row["stake"] == 10  # what was there is untouched
        # Columns added since exist, with their defaults.
        assert (row["slip_message_id"], row["backfilled"], row["skipped"], row["slates_posted"]) == (None, 0, 0, "[]")
        await db.update_parlay(2026, 6, "main", slip_message_id=1234, backfilled=1)
        assert (await db.parlay(2026, 6, "main"))["slip_message_id"] == 1234
        await db.close()

        # Opening it again changes nothing and does not fail.
        again = Database(path)
        await again.connect()
        assert (await again.parlay(2026, 6, "main"))["slip_message_id"] == 1234
        await again.close()

    run(scenario())


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
        picks = await db.parlay_picks(2026, 6, "main")
        assert len(picks) == 1
        # The replacement starts ungraded.
        assert (picks[0]["id"], picks[0]["label"], picks[0]["status"]) == (second_id, "PHI moneyline", "pending")
        await db.close()

    run(scenario())


def test_pending_picks_and_progress():
    async def scenario():
        db = await fresh()
        live, _ = await db.save_pick(pick(1))
        graded, _ = await db.save_pick(pick(2))
        await db.grade_pick(graded, "lost", 12, None, "auto")
        assert [p["id"] for p in await db.parlay_picks(2026, 6, "main") if p["status"] == "pending"] == [live]

        await db.set_progress(live, 31)
        await db.set_progress(graded, 99)  # a settled leg keeps its final number
        assert (await db.pick(live))["stat_value"] == 31
        assert (await db.pick(graded))["stat_value"] == 12

        moved = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)
        await db.set_kickoff(live, moved)
        assert (await db.pick(live))["kickoff"] == "2026-10-19T23:00:00+00:00"

        # Sending a graded leg back to pending clears when it was graded.
        await db.grade_pick(graded, "pending", None, None, "42")
        assert (await db.pick(graded))["graded_at"] is None
        await db.close()

    run(scenario())


def test_parlay_updates_reject_unknown_columns():
    async def scenario():
        db = await fresh()
        row = await db.ensure_parlay(2026, 6, "main")
        assert row["announced_at"] is None and row["reminders_sent"] == "[]" and row["slates_posted"] == "[]"
        await db.update_parlay(2026, 6, "main", stake=10, placed_odds=125000)
        assert (await db.parlay(2026, 6, "main"))["placed_odds"] == 125000
        with pytest.raises(ValueError):
            await db.update_parlay(2026, 6, "main", nonsense=1)
        await db.close()

    run(scenario())


def test_a_week_can_hold_two_parlays():
    async def scenario():
        db = await fresh()
        await db.ensure_parlay(2026, 12, "main")
        await db.ensure_parlay(2026, 12, "bonus")
        await db.ensure_parlay(2026, 12, "bonus")  # asking twice makes one row
        await db.update_parlay(2026, 12, "bonus", placed_odds=5000)
        assert [row["card"] for row in await db.parlays(2026, 12)] == ["bonus", "main"]
        assert (await db.parlay(2026, 12, "main"))["placed_odds"] is None
        assert len(await db.parlays(2026)) == 2 and len(await db.parlays()) == 2
        assert await db.parlays(2025) == []

        # One member holds a pick in each; replacing one leaves the other alone.
        await db.save_pick(pick(1, week=12, card="bonus", label="DET -2.5"))
        monday, _ = await db.save_pick(pick(1, week=12, card="main", label="TB moneyline"))
        _, replaced = await db.save_pick(pick(1, week=12, card="bonus", label="DET moneyline"))
        assert replaced is True
        assert [p["label"] for p in await db.parlay_picks(2026, 12, "bonus")] == ["DET moneyline"]
        assert (await db.member_pick(2026, 12, "main", 1))["id"] == monday

        assert await db.seasons() == [2026]
        await db.ensure_parlay(2027, 1, "main")
        await db.ensure_parlay(2025, 9, "main")
        assert await db.seasons() == [2027, 2026, 2025]  # newest first
        assert [row["week"] for row in await db.parlays(2026)] == [12, 12]

        await db.add_no_show(2026, 12, "bonus", 3)
        await db.add_no_show(2026, 12, "main", 3)
        await db.add_no_show(2026, 12, "main", 3)
        assert len(await db.no_shows(2026)) == 2
        await db.close()

    run(scenario())


def test_debt_and_payment():
    async def scenario():
        db = await fresh()
        await db.set_debt(2026, 6, roster_id=3, team_name="Team Three", amount=10, discord_id=300)
        assert (await db.debt(2026, 6))["paid_at"] is None
        assert (await db.debts(2026))[0]["discord_id"] == 300
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
