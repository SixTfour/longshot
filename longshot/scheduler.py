"""The clock. Once a minute, run whatever is due.

Nothing here remembers what it did: every step looks at the database to see
whether it has already run. So a bot that was switched off simply catches up
the next time it starts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from discord.ext import tasks

from . import tracking, weekly
from .clients.espn import EspnError
from .db import utcnow

if TYPE_CHECKING:
    from .bot import Longshot

log = logging.getLogger(__name__)

TRACK_EVERY = timedelta(minutes=3)


class Scheduler:
    def __init__(self, bot: Longshot):
        self.bot = bot
        self._last_track: datetime | None = None

    def start(self) -> None:
        if not self.tick.is_running():
            self.tick.start()

    def stop(self) -> None:
        self.tick.cancel()

    @tasks.loop(seconds=60)
    async def tick(self) -> None:
        try:
            await self.run_due(utcnow())
        except EspnError as error:
            log.warning("Skipping this tick: %s", error)
        except Exception:
            log.exception("The scheduler tick failed")

    @tick.before_loop
    async def _wait_until_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def run_due(self, now: datetime) -> None:
        bot = self.bot
        if await bot.league_id() is None:
            return  # /setup has not been run yet

        active = await bot.nfl.active(now)
        if active is not None:
            await bot.nfl.warm(active.games)  # rosters for /pick; a no-op once loaded
            await weekly.announce(bot, active, now)
            await weekly.remind(bot, active, now)
            await weekly.close_picks(bot, active, now)

        # An earlier week left open because the bot was off across its deadline.
        for row in await bot.db.weeks():
            is_active = active is not None and (row["season"], row["week"]) == (active.season, active.week)
            if row["announced_at"] and not row["closed_at"] and not is_active:
                context = await bot.nfl.context(row["season"], row["week"])
                if context is not None:
                    await weekly.close_picks(bot, context, now)

        if self._last_track is None or now - self._last_track >= TRACK_EVERY:
            self._last_track = now
            await tracking.track(bot, now)

        for row in await bot.db.weeks():
            if row["closed_at"] and not row["recap_at"]:
                await weekly.recap(bot, row["season"], row["week"])
