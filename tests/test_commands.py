"""Every slash command registers and fits Discord's limits, checked without connecting to Discord."""

import asyncio

from world import build

EXPECTED = {
    "setup", "link", "unlink", "members", "config", "pick", "pickfor", "mypick", "slip", "placed", "grade",
    "paid", "ledger", "stats", "leaderboard", "admin",
}
COMMISSIONER_ONLY = {"setup", "link", "unlink", "config", "pickfor", "placed", "grade", "paid", "admin"}


def _walk(payload: dict):
    """Yield a command payload and every subcommand and option inside it."""
    yield payload
    for option in payload.get("options", []):
        yield from _walk(option)


def test_commands_register_within_discord_limits(tmp_path):
    async def scenario():
        world = await build(tmp_path)
        try:
            tree = world.bot.tree
            commands = {command.name: command for command in tree.get_commands()}
            assert set(commands) == EXPECTED

            for name, command in commands.items():
                payload = command.to_dict(tree)
                for node in _walk(payload):
                    assert 1 <= len(node["name"]) <= 32, node["name"]
                    assert 1 <= len(node["description"]) <= 100, f"{name}: {node['description']}"
                    assert len(node.get("options", [])) <= 25
                    for choice in node.get("choices", []):
                        assert len(choice["name"]) <= 100 and len(str(choice["value"])) <= 100
                # Hidden from ordinary members by default, on top of the check that runs when used.
                hidden = payload.get("default_member_permissions") is not None
                assert hidden == (name in COMMISSIONER_ONLY), name

            admin = commands["admin"].to_dict(tree)
            assert {option["name"] for option in admin["options"]} == {"run", "loser", "status"}
            pick = commands["pick"].to_dict(tree)
            assert [(o["name"], o["required"], o["autocomplete"]) for o in pick["options"]] == [
                ("who", True, True), ("bet", True, True), ("line", True, True),
            ]
        finally:
            await world.close()

    asyncio.run(scenario())
