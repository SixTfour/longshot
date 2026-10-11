"""Commands only run in the league channel chosen with /setup."""

import asyncio
from types import SimpleNamespace

import discord
from world import build

LEAGUE, ELSEWHERE, THREAD = 555, 777, 888


def with_bot(tmp_path, scenario):
    async def run():
        world = await build(tmp_path)
        try:
            await scenario(world.bot)
        finally:
            await world.close()

    asyncio.run(run())


class FakeInteraction:
    """A command, or the list shown while one is being typed, arriving from some channel."""

    def __init__(self, command, channel_id, *, parent_id=None, autocomplete=False):
        self.data = {"name": command}
        self.channel_id = channel_id
        self.channel = SimpleNamespace(id=channel_id, parent_id=parent_id)
        self.type = discord.InteractionType.autocomplete if autocomplete else discord.InteractionType.application_command
        self.replies, self.lists = [], []
        self.response = SimpleNamespace(send_message=self._reply, autocomplete=self._list)

    async def _reply(self, content, **kwargs):
        self.replies.append((content, kwargs))

    async def _list(self, choices):
        self.lists.append(choices)


def test_where_a_command_may_run(tmp_path):
    async def scenario(bot):
        # Until /setup has chosen a channel there is nothing to restrict to.
        assert await bot.misplaced("pick", ELSEWHERE) is None

        await bot.db.set_state("channel_id", LEAGUE)
        assert await bot.misplaced("pick", LEAGUE) is None
        assert await bot.misplaced("pick", ELSEWHERE) == LEAGUE
        assert await bot.misplaced("admin", ELSEWHERE) == LEAGUE  # commissioner commands too
        assert await bot.misplaced("pick", THREAD, parent_id=LEAGUE) is None  # a thread in the channel
        assert await bot.misplaced("pick", THREAD, parent_id=ELSEWHERE) == LEAGUE
        assert await bot.misplaced("setup", ELSEWHERE) is None  # so the channel can be changed

    with_bot(tmp_path, scenario)


def test_a_command_used_elsewhere_is_turned_away_privately(tmp_path):
    async def scenario(bot):
        await bot.db.set_state("channel_id", LEAGUE)

        here = FakeInteraction("pick", LEAGUE)
        assert await bot.tree.interaction_check(here) is True
        assert here.replies == []

        there = FakeInteraction("pick", ELSEWHERE)
        assert await bot.tree.interaction_check(there) is False
        assert there.replies == [("Longshot commands only work in <#555>.", {"ephemeral": True})]

        # While a command is being typed elsewhere, its lists come back empty.
        typing = FakeInteraction("pick", ELSEWHERE, autocomplete=True)
        assert await bot.tree.interaction_check(typing) is False
        assert typing.lists == [[]] and typing.replies == []

        moving = FakeInteraction("setup", ELSEWHERE)
        assert await bot.tree.interaction_check(moving) is True

    with_bot(tmp_path, scenario)


def test_every_command_goes_through_the_check(tmp_path):
    async def scenario(bot):
        assert type(bot.tree).__name__ == "LeagueTree"
        assert len(bot.tree.get_commands()) == 17

    with_bot(tmp_path, scenario)
