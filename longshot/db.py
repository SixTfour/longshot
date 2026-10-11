"""SQLite storage. All timestamps are ISO 8601 strings in UTC."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Internal values the commissioner does not edit (league id, channel id, credit readings).
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Responses from the odds feed and ESPN, kept to save credits and requests.
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    roster_id       INTEGER PRIMARY KEY,
    discord_id      INTEGER NOT NULL UNIQUE,
    team_name       TEXT NOT NULL,
    sleeper_user_id TEXT
);

CREATE TABLE IF NOT EXISTS weeks (
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    stake           REAL,
    loser_roster_id INTEGER,
    loser_team      TEXT,
    loser_points    REAL,
    tied_roster_ids TEXT,
    announced_at    TEXT,
    reminders_sent  TEXT NOT NULL DEFAULT '[]',
    deadline_at     TEXT,
    closed_at       TEXT,
    skipped         INTEGER NOT NULL DEFAULT 0,
    placed_at       TEXT,
    placed_odds     INTEGER,
    killer_pick_id  INTEGER,
    result          TEXT,
    payout          REAL,
    recap_at        TEXT,
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS picks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    discord_id    INTEGER NOT NULL,
    espn_event_id TEXT NOT NULL,
    game          TEXT NOT NULL,
    kickoff       TEXT NOT NULL,
    home_abbr     TEXT NOT NULL,
    away_abbr     TEXT NOT NULL,
    market        TEXT NOT NULL,
    side          TEXT NOT NULL,
    line          REAL,
    price         INTEGER NOT NULL,
    alt           INTEGER NOT NULL DEFAULT 0,
    team_abbr     TEXT,
    player_id     TEXT,
    player_name   TEXT,
    label         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    stat_value    REAL,
    note          TEXT,
    graded_at     TEXT,
    graded_by     TEXT,
    entered_by    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (season, week, discord_id)
);

CREATE TABLE IF NOT EXISTS no_shows (
    season     INTEGER NOT NULL,
    week       INTEGER NOT NULL,
    discord_id INTEGER NOT NULL,
    PRIMARY KEY (season, week, discord_id)
);

-- kind is 'debt' (the week's loser owes the stake) or 'payment' (it was collected).
-- A payment carries the season and week of the debt it settles.
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season     INTEGER NOT NULL,
    week       INTEGER NOT NULL,
    roster_id  INTEGER NOT NULL,
    team_name  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""

PICK_COLUMNS = (
    "season", "week", "discord_id", "espn_event_id", "game", "kickoff", "home_abbr", "away_abbr",
    "market", "side", "line", "price", "alt", "team_abbr", "player_id", "player_name", "label",
    "entered_by",
)

WEEK_COLUMNS = {
    "stake", "loser_roster_id", "loser_team", "loser_points", "tied_roster_ids", "announced_at",
    "reminders_sent", "deadline_at", "closed_at", "skipped", "placed_at", "placed_odds",
    "killer_pick_id", "result", "payout", "recap_at",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Database:
    def __init__(self, path: Path | str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if str(self.path) != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def _run(self, sql: str, params: tuple = ()) -> int:
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor.lastrowid

    # --- settings, state, cache -------------------------------------------------

    async def get_setting(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._run(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_state(self, key: str, default: str | None = None) -> str | None:
        row = await self._one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set_state(self, key: str, value: object) -> None:
        await self._run(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    async def cache_get(self, key: str) -> tuple[Any, datetime] | None:
        row = await self._one("SELECT payload, fetched_at FROM cache WHERE key = ?", (key,))
        if row is None:
            return None
        return json.loads(row["payload"]), parse_iso(row["fetched_at"])

    async def cache_put(self, key: str, payload: Any, fetched_at: datetime | None = None) -> None:
        await self._run(
            "INSERT INTO cache (key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
            (key, json.dumps(payload), iso(fetched_at or utcnow())),
        )

    # --- members ----------------------------------------------------------------

    async def link_member(
        self, roster_id: int, discord_id: int, team_name: str, sleeper_user_id: str | None
    ) -> None:
        # A Discord user holds one team and a team has one Discord user.
        await self.conn.execute(
            "DELETE FROM members WHERE roster_id = ? OR discord_id = ?", (roster_id, discord_id)
        )
        await self._run(
            "INSERT INTO members (roster_id, discord_id, team_name, sleeper_user_id) VALUES (?, ?, ?, ?)",
            (roster_id, discord_id, team_name, sleeper_user_id),
        )

    async def unlink_member(self, discord_id: int) -> bool:
        cursor = await self.conn.execute("DELETE FROM members WHERE discord_id = ?", (discord_id,))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def members(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM members ORDER BY team_name COLLATE NOCASE")

    async def member(self, discord_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM members WHERE discord_id = ?", (discord_id,))

    async def member_by_roster(self, roster_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM members WHERE roster_id = ?", (roster_id,))

    # --- weeks ------------------------------------------------------------------

    async def week(self, season: int, week: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM weeks WHERE season = ? AND week = ?", (season, week))

    async def ensure_week(self, season: int, week: int) -> dict[str, Any]:
        await self._run("INSERT OR IGNORE INTO weeks (season, week) VALUES (?, ?)", (season, week))
        return await self.week(season, week)

    async def update_week(self, season: int, week: int, **fields: Any) -> None:
        unknown = set(fields) - WEEK_COLUMNS
        if unknown:
            raise ValueError(f"Unknown week columns: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        await self._run(
            f"UPDATE weeks SET {assignments} WHERE season = ? AND week = ?",
            (*fields.values(), season, week),
        )

    async def weeks(self, season: int | None = None) -> list[dict[str, Any]]:
        if season is None:
            return await self._all("SELECT * FROM weeks ORDER BY season, week")
        return await self._all("SELECT * FROM weeks WHERE season = ? ORDER BY week", (season,))

    # --- picks ------------------------------------------------------------------

    async def save_pick(self, pick: dict[str, Any]) -> tuple[int, bool]:
        """Insert or replace a member's pick for the week. Returns (id, replaced)."""
        existing = await self.member_pick(pick["season"], pick["week"], pick["discord_id"])
        if existing:
            await self.conn.execute("DELETE FROM picks WHERE id = ?", (existing["id"],))
        columns = ", ".join(PICK_COLUMNS)
        marks = ", ".join("?" for _ in PICK_COLUMNS)
        pick_id = await self._run(
            f"INSERT INTO picks ({columns}, created_at) VALUES ({marks}, ?)",
            (*(pick.get(name) for name in PICK_COLUMNS), iso(utcnow())),
        )
        return pick_id, existing is not None

    async def pick(self, pick_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM picks WHERE id = ?", (pick_id,))

    async def member_pick(self, season: int, week: int, discord_id: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM picks WHERE season = ? AND week = ? AND discord_id = ?",
            (season, week, discord_id),
        )

    async def week_picks(self, season: int, week: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM picks WHERE season = ? AND week = ? ORDER BY kickoff, id", (season, week)
        )

    async def all_picks(self, season: int | None = None) -> list[dict[str, Any]]:
        if season is None:
            return await self._all("SELECT * FROM picks ORDER BY season, week, kickoff, id")
        return await self._all(
            "SELECT * FROM picks WHERE season = ? ORDER BY week, kickoff, id", (season,)
        )

    async def started_pending_picks(self, now: datetime) -> list[dict[str, Any]]:
        """Ungraded picks whose game has kicked off."""
        return await self._all(
            "SELECT * FROM picks WHERE status = 'pending' AND kickoff <= ? ORDER BY kickoff, id",
            (iso(now),),
        )

    async def grade_pick(
        self, pick_id: int, status: str, stat_value: float | None, note: str | None, graded_by: str
    ) -> None:
        graded_at = None if status == "pending" else iso(utcnow())
        await self._run(
            "UPDATE picks SET status = ?, stat_value = ?, note = ?, graded_at = ?, graded_by = ? WHERE id = ?",
            (status, stat_value, note, graded_at, graded_by, pick_id),
        )

    # --- no-shows ---------------------------------------------------------------

    async def add_no_show(self, season: int, week: int, discord_id: int) -> None:
        await self._run(
            "INSERT OR IGNORE INTO no_shows (season, week, discord_id) VALUES (?, ?, ?)",
            (season, week, discord_id),
        )

    async def no_shows(self, season: int | None = None) -> list[dict[str, Any]]:
        if season is None:
            return await self._all("SELECT * FROM no_shows")
        return await self._all("SELECT * FROM no_shows WHERE season = ?", (season,))

    # --- ledger -----------------------------------------------------------------

    async def set_debt(self, season: int, week: int, roster_id: int, team_name: str, amount: float) -> None:
        """Record who owes the stake for a week, replacing any earlier answer."""
        current = await self.debt(season, week)
        if current and current["roster_id"] == roster_id and current["amount"] == amount:
            return
        await self.conn.execute("DELETE FROM ledger WHERE season = ? AND week = ?", (season, week))
        await self._run(
            "INSERT INTO ledger (season, week, roster_id, team_name, kind, amount, created_at) "
            "VALUES (?, ?, ?, ?, 'debt', ?, ?)",
            (season, week, roster_id, team_name, amount, iso(utcnow())),
        )

    async def debt(self, season: int, week: int) -> dict[str, Any] | None:
        row = await self._one(
            "SELECT * FROM ledger WHERE season = ? AND week = ? AND kind = 'debt'", (season, week)
        )
        if row is None:
            return None
        paid = await self._one(
            "SELECT created_at FROM ledger WHERE season = ? AND week = ? AND kind = 'payment'",
            (season, week),
        )
        row["paid_at"] = paid["created_at"] if paid else None
        return row

    async def debts(self, season: int | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT d.*, p.created_at AS paid_at FROM ledger d "
            "LEFT JOIN ledger p ON p.kind = 'payment' AND p.season = d.season AND p.week = d.week "
            "WHERE d.kind = 'debt'"
        )
        if season is None:
            return await self._all(sql + " ORDER BY d.season, d.week")
        return await self._all(sql + " AND d.season = ? ORDER BY d.week", (season,))

    async def mark_paid(self, season: int, week: int) -> bool:
        """Settle a week's debt. Returns False if there is none or it is already paid."""
        debt = await self.debt(season, week)
        if debt is None or debt["paid_at"]:
            return False
        await self._run(
            "INSERT INTO ledger (season, week, roster_id, team_name, kind, amount, created_at) "
            "VALUES (?, ?, ?, ?, 'payment', ?, ?)",
            (season, week, debt["roster_id"], debt["team_name"], debt["amount"], iso(utcnow())),
        )
        return True
