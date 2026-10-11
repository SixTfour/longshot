"""Starting up: registering the slash commands with Discord, and failing clearly when Discord refuses."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import discord
import pytest
from world import build

from longshot.config import ConfigError


def run_with_bot(tmp_path, scenario):
    async def run():
        world = await build(tmp_path)
        try:
            await scenario(world.bot)
        finally:
            await world.close()

    asyncio.run(run())


def test_commands_are_registered_everywhere_when_no_server_is_named(tmp_path):
    async def scenario(bot):
        calls = []

        async def sync(*, guild=None):
            calls.append(guild)
            return []

        bot.tree.sync = sync
        await bot.sync_commands()
        assert calls == [None]

    run_with_bot(tmp_path, scenario)


def test_commands_are_registered_in_the_named_server(tmp_path):
    async def scenario(bot):
        calls = []

        async def sync(*, guild=None):
            calls.append(guild.id if guild else None)
            return []

        bot.tree.sync = sync
        bot.config = replace(bot.config, guild_id=1234567890)
        await bot.sync_commands()
        assert calls == [1234567890]
        assert {command.name for command in bot.tree.get_commands(guild=discord.Object(1234567890))} >= {"pick", "setup"}

    run_with_bot(tmp_path, scenario)


def test_a_server_the_bot_cannot_reach_is_explained_not_dumped(tmp_path):
    async def scenario(bot):
        async def sync(*, guild=None):
            # What Discord answers when the bot is not in the server or lacks the applications.commands scope.
            response = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(response, {"code": 50001, "message": "Missing Access"})

        bot.tree.sync = sync
        bot.config = replace(bot.config, guild_id=1234567890)
        with pytest.raises(ConfigError) as caught:
            await bot.sync_commands()
        message = str(caught.value)
        assert "GUILD_ID (1234567890)" in message
        assert "applications.commands" in message and "Copy Server ID" in message
        assert "leave GUILD_ID empty" in message

    run_with_bot(tmp_path, scenario)
