"""Longshot's messages name members by their fantasy team, and mention them only to notify."""

from test_week_flow import (
    DEN_SPREAD,
    IRVING_150,
    THU_DEADLINE,
    THU_LATE,
    TUE_ANNOUNCE,
    TUE_NOON,
    WED_EVENING,
    play,
)
from world import TNF

from longshot import render


def test_a_member_is_named_by_team_and_an_unlinked_one_falls_back_to_a_mention():
    names = {1: "Hurts So Good", 2: "Kelce Grammar"}
    assert render.team(1, names) == "Hurts So Good"
    assert render.team(9, names) == "<@9>"
    assert render.teams([1, 2], names) == "**Hurts So Good**, **Kelce Grammar**"


def has_mention(message) -> bool:
    return "<@" in message.content or "<@" in (message.embed.description if getattr(message, "embed", None) else "")


def test_only_the_announcement_and_reminders_mention_anyone(tmp_path, monkeypatch):
    async def scenario(world, at):
        channel = world.channel

        # The announcement names the lowest scorer by team, then mentions everyone on a last line of its own.
        await at(TUE_ANNOUNCE)
        announcement = channel.sent[-1].content.splitlines()
        assert "**Team 2** had the lowest score of Week 5" in announcement[1]
        assert announcement[-1] == "<@1> <@2> <@3>"
        assert sum("<@" in line for line in announcement) == 1

        # Picks are announced by team name.
        await at(TUE_NOON, tick=False)
        await world.pick(1, *IRVING_150)
        await world.pick(2, *DEN_SPREAD)
        assert channel.last().startswith("🔒 **Team 2** locked in **DEN -2.5** (-110)")
        assert not has_mention(channel.sent[-1])

        # A reminder names the team in its text and mentions the member underneath, so they are notified.
        await at(WED_EVENING)
        text, ping = channel.last().splitlines()
        assert "Still waiting on **Team 3**. Use `/pick`." in text and "<@" not in text
        assert ping == "<@3>"
        assert channel.sent[-1].allowed_mentions is render.PING_USERS

        # From the deadline on, nothing mentions anyone: the slip, results and the death notice use team names.
        before = len(channel.sent)
        await at(THU_DEADLINE)
        world.espn.set(TNF, state="post", home=21, away=20)  # Denver does not cover
        await at(THU_LATE)
        later = channel.sent[before:]
        assert len(later) == 3  # the slip, the missed leg, the death notice
        assert not any(has_mention(message) for message in later)
        assert all(message.allowed_mentions is render.QUIET for message in later)
        assert "No pick from **Team 3**" in later[0].content
        assert "❌ **Team 2** · DEN -2.5 (-110)" in channel.message(later[0].id).embed.description
        assert later[1].content == "❌ **Leg missed** · Team 2 · DEN -2.5 (-110)"
        assert later[2].content == "💀 **The Week 6 parlay is dead.** Team 2's leg missed: DEN -2.5 (-110)."

    play(tmp_path, monkeypatch, scenario)
