"""Sleeper's public read-only API. No key is needed."""

from __future__ import annotations

from typing import Any

import aiohttp

BASE = "https://api.sleeper.app/v1"


class SleeperError(RuntimeError):
    pass


class SleeperClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def _get(self, path: str, *, may_be_empty: bool = False) -> Any:
        try:
            async with self._session.get(f"{BASE}{path}") as response:
                if response.status == 404:
                    raise SleeperError("Sleeper has no league with that ID.")
                if response.status != 200:
                    raise SleeperError(f"Sleeper returned HTTP {response.status}.")
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError) as error:
            raise SleeperError(f"Could not reach Sleeper: {error or type(error).__name__}") from error
        if data is None and not may_be_empty:
            raise SleeperError("Sleeper has no league with that ID.")
        return data

    async def league(self, league_id: str) -> dict[str, Any]:
        return await self._get(f"/league/{league_id}")

    async def users(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/users")

    async def rosters(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/rosters")

    async def matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/matchups/{week}")

    async def winners_bracket(self, league_id: str) -> list[dict[str, Any]]:
        """The playoff bracket: one entry per matchup, with its round.

        Empty if Sleeper has not built it yet or will not serve it. The bracket only
        refines when the playoffs end, so a league is never held up for want of it.
        """
        try:
            return await self._get(f"/league/{league_id}/winners_bracket", may_be_empty=True) or []
        except SleeperError:
            return []

    async def teams(self, league_id: str) -> list[dict[str, Any]]:
        """One entry per roster: roster_id, owner's Sleeper user id, and a display name."""
        users = {user["user_id"]: user for user in await self.users(league_id)}
        teams = []
        for roster in await self.rosters(league_id):
            owner = users.get(roster.get("owner_id")) or {}
            handle = owner.get("display_name") or f"Roster {roster['roster_id']}"
            team_name = (owner.get("metadata") or {}).get("team_name")
            teams.append(
                {
                    "roster_id": int(roster["roster_id"]),
                    "sleeper_user_id": roster.get("owner_id"),
                    "team_name": team_name or handle,
                    "handle": handle,
                }
            )
        return sorted(teams, key=lambda team: team["team_name"].lower())
