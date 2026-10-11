"""The bot itself: wires the database, the API clients and the commands together."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .clients.espn import EspnClient, EspnError
from .clients.odds import OddsApiError
from .clients.sleeper import SleeperClient, SleeperError
from .config import Config
from .db import Database, utcnow
from .domain.fantasy import FantasySeason, fantasy_season
from .linebook import LineBook, NoCredits
from .nfl import Nfl
from .scheduler import Scheduler
from .settings import Settings
from .views import PaidButton

log = logging.getLogger(__name__)

COGS = ("setup", "config", "picks", "backfill", "slip", "ledger", "stats", "admin")
FANTASY_TTL = timedelta(hours=6)  # how long the league's season details are reused


class NotCommissioner(app_commands.CheckFailure):
    pass


def commissioner_only():
    """Restrict a slash command to the commissioner (or a server manager)."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if await interaction.client.is_commissioner(interaction):
            return True
        raise NotCommissioner()

    return app_commands.check(predicate)


class Longshot(commands.Bot):
    def __init__(self, config: Config):
        # No privileged intents: everything runs on slash commands and buttons.
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(), help_command=None)
        self.config = config
        self.db = Database(config.db_path)
        self.settings = Settings(self.db, config.setting_defaults)
        self.scheduler = Scheduler(self)
        self.web: aiohttp.ClientSession | None = None
        self._fantasy: tuple[str, FantasySeason, datetime] | None = None

    async def wire(self) -> None:
        """Open the database, build the clients and register every command. Does not touch Discord."""
        self.settings.check_defaults()
        await self.db.connect()
        self.web = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.sleeper = SleeperClient(self.web)
        self.espn = EspnClient(self.web)
        self.nfl = Nfl(self.espn, self.db, self.settings)
        self.linebook = LineBook(self.web, self.config.odds_api_key, self.db, self.settings, self._credits_low)

        for name in COGS:
            await self.load_extension(f"longshot.cogs.{name}")
        self.add_dynamic_items(PaidButton)
        self.tree.on_error = self._on_command_error

    async def setup_hook(self) -> None:
        await self.wire()
        if self.config.guild_id:
            # Guild commands appear instantly, which suits testing.
            guild = discord.Object(self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.scheduler.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s. Database: %s", self.user, self.config.db_path)

    async def close(self) -> None:
        self.scheduler.stop()
        await super().close()
        if self.web is not None:
            await self.web.close()
        await self.db.close()

    # --- league state -----------------------------------------------------------

    async def league_id(self) -> str | None:
        return await self.db.get_state("league_id")

    async def fantasy(self) -> FantasySeason | None:
        """When the connected league's fantasy season runs, from Sleeper. None until /setup has been run."""
        league_id = await self.league_id()
        if league_id is None:
            return None
        held = self._fantasy
        if held is None or held[0] != league_id or utcnow() - held[2] > FANTASY_TTL:
            league = await self.sleeper.league(league_id)
            bracket = await self.sleeper.winners_bracket(league_id)
            self._fantasy = held = (league_id, fantasy_season(league, bracket), utcnow())
        return held[1]

    def forget_league(self) -> None:
        """Drop what is remembered about the league, after /setup connects a different one."""
        self._fantasy = None

    async def league_channel(self) -> discord.abc.Messageable | None:
        channel_id = await self.db.get_state("channel_id")
        if channel_id is None:
            return None
        channel = self.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.fetch_channel(int(channel_id))
            except discord.HTTPException:
                log.warning("The league channel %s cannot be reached.", channel_id)
                return None
        return channel

    async def is_commissioner(self, interaction: discord.Interaction) -> bool:
        commissioner = await self.db.get_state("commissioner_id")
        if commissioner is not None and int(commissioner) == interaction.user.id:
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.manage_guild)

    async def tell_commissioner(self, text: str) -> None:
        """A private note to the commissioner, falling back to the league channel."""
        commissioner = await self.db.get_state("commissioner_id")
        if commissioner is not None:
            try:
                user = self.get_user(int(commissioner)) or await self.fetch_user(int(commissioner))
                await user.send(text)
                return
            except discord.HTTPException:
                pass
        channel = await self.league_channel()
        if channel is not None:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())

    async def _credits_low(self, remaining: int) -> None:
        await self.tell_commissioner(
            f"⚠️ Longshot has **{remaining}** Odds API credits left this month. To keep the rest in reserve it "
            "has stopped fetching new lines; bets it has already looked up still work. Credits reset monthly, "
            "or raise the limit at the-odds-api.com."
        )

    # --- errors -----------------------------------------------------------------

    async def _on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        cause = getattr(error, "original", error)
        if isinstance(error, NotCommissioner):
            text = "Only the commissioner can use that command."
        elif isinstance(cause, NoCredits):
            text = "The odds feed is out of credits for now, so new lines cannot be looked up. Ask the commissioner."
        elif isinstance(cause, (OddsApiError, SleeperError, EspnError)):
            text = f"A data source did not respond: {cause}. Try again in a minute."
        else:
            log.error("Command %s failed", getattr(interaction.command, "qualified_name", "?"), exc_info=error)
            text = "Something went wrong running that command. It has been logged."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass
