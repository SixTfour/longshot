"""The scheduled steps of a week: announce the loser, remind, close picks, recap.

Each step first checks the week's row to see whether it has already run, so
calling them every minute, or again after a restart, is safe.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord

from . import render
from .clients.sleeper import SleeperError
from .db import iso
from .domain.grading import LOST, PENDING, VOID, WON, parlay_status
from .domain.loser import lowest_scorers
from .domain.odds_math import american_to_decimal, format_american, format_money, total_return
from .domain.schedule import stamp
from .nfl import WeekContext
from .views import paid_view

if TYPE_CHECKING:
    from .bot import Longshot

log = logging.getLogger(__name__)


async def record_loser(bot: Longshot, season: int, week: int, team: dict[str, Any], points: float | None) -> None:
    """Store who owes the stake for a week's parlay."""
    stake = await bot.settings.stake()
    await bot.db.update_week(
        season, week, stake=stake, loser_roster_id=team["roster_id"], loser_team=team["team_name"],
        loser_points=points, tied_roster_ids=None,
    )
    await bot.db.set_debt(season, week, team["roster_id"], team["team_name"], stake)


async def _team_text(bot: Longshot, team: dict[str, Any]) -> str:
    member = await bot.db.member_by_roster(team["roster_id"])
    return f"**{team['team_name']}** ({render.mention(member['discord_id'])})" if member else f"**{team['team_name']}**"


async def announce(bot: Longshot, ctx: WeekContext, now: datetime, *, force: bool = False) -> bool:
    """Post last week's lowest scorer and open picks."""
    row = await bot.db.ensure_week(ctx.season, ctx.week)
    if row["announced_at"] and not force:
        return False
    league_id, channel = await bot.league_id(), await bot.league_channel()
    if league_id is None or channel is None:
        return False

    stake = await bot.settings.stake()
    lines = [f"## Week {ctx.week} parlay"]
    view = None
    pings: list[int] = []

    if ctx.week == 1:
        lines.append("It is Week 1, so there is no lowest scorer yet.")
    else:
        try:
            teams = {team["roster_id"]: team for team in await bot.sleeper.teams(league_id)}
            low = lowest_scorers(await bot.sleeper.matchups(league_id, ctx.week - 1))
        except SleeperError as error:
            log.warning("Could not read Week %s scores from Sleeper: %s", ctx.week - 1, error)
            return False

        tied = [teams[roster] for roster in (low[1] if low else []) if roster in teams]
        if low is None or not tied:
            lines.append(
                f"Sleeper has no scores for Week {ctx.week - 1}. Commissioner: set who pays with `/admin loser`."
            )
        elif len(tied) == 1:
            await record_loser(bot, ctx.season, ctx.week, tied[0], low[0])
            member = await bot.db.member_by_roster(tied[0]["roster_id"])
            if member:
                pings.append(member["discord_id"])
            lines.append(
                f"{await _team_text(bot, tied[0])} had the lowest score of Week {ctx.week - 1} with "
                f"**{low[0]:g}** points and owes **{format_money(stake)}** for this week's parlay."
            )
            view = paid_view(ctx.season, ctx.week)
        else:
            names = ", ".join([await _team_text(bot, team) for team in tied])
            await bot.db.update_week(
                ctx.season, ctx.week, stake=stake, loser_points=low[0],
                tied_roster_ids=json.dumps([team["roster_id"] for team in tied]),
            )
            lines.append(
                f"Week {ctx.week - 1}'s lowest score was a tie at **{low[0]:g}** points: {names}. "
                "Commissioner: choose who pays with `/admin loser`."
            )

    deadline = ctx.times.deadline_at
    if now < deadline:
        book = await bot.settings.sportsbook_name()
        members = [member["discord_id"] for member in await bot.db.members()]
        pings.extend(members)
        lines.append(
            f"\n**Picks are open** until {stamp(deadline, 'F')} ({stamp(deadline, 'R')}). "
            f"Find your bet on {book}, then lock it in with `/pick`."
        )
        if ctx.times.moved_early:
            lines.append(
                "This week's first game kicks off earlier than usual, so the deadline is an hour before that kickoff."
            )
        if members:
            lines.append(render.mentions(members))
        await bot.db.update_week(ctx.season, ctx.week, stake=stake, announced_at=iso(now))
    else:
        # The bot was not running (or not set up) before this week's deadline.
        lines.append("\nThis week's pick deadline has already passed, so no picks are being collected.")
        await bot.db.update_week(
            ctx.season, ctx.week, stake=stake, announced_at=iso(now), closed_at=iso(now),
            deadline_at=iso(deadline), skipped=1,
        )

    await channel.send(
        "\n".join(lines),
        view=view,
        allowed_mentions=discord.AllowedMentions(users=[discord.Object(id) for id in set(pings)]),
    )
    return True


async def missing_members(bot: Longshot, season: int, week: int) -> list[int]:
    picked = {pick["discord_id"] for pick in await bot.db.week_picks(season, week)}
    return [member["discord_id"] for member in await bot.db.members() if member["discord_id"] not in picked]


async def remind(bot: Longshot, ctx: WeekContext, now: datetime, *, force: bool = False) -> bool:
    """Nudge members who have not picked, in the channel and by DM."""
    row = await bot.db.week(ctx.season, ctx.week)
    if row is None or not row["announced_at"] or row["closed_at"]:
        return False
    deadline = ctx.times.deadline_at
    sent = json.loads(row["reminders_sent"])
    due = [hours for hours, at in ctx.times.reminders.items() if at <= now < deadline and hours not in sent]
    if not due and not force:
        return False
    # If several came due while the bot was offline, one reminder covers them all.
    await bot.db.update_week(ctx.season, ctx.week, reminders_sent=json.dumps(sorted(set(sent) | set(due))))

    missing = await missing_members(bot, ctx.season, ctx.week)
    channel = await bot.league_channel()
    if not missing or channel is None:
        return False
    await channel.send(
        f"⏰ Week {ctx.week} picks close {stamp(deadline, 'R')}. Still waiting on {render.mentions(missing)}. Use `/pick`.",
        allowed_mentions=render.PING_USERS,
    )
    for discord_id in missing:
        try:
            user = bot.get_user(discord_id) or await bot.fetch_user(discord_id)
            await user.send(
                f"Your Week {ctx.week} parlay pick is due {stamp(deadline, 'F')} ({stamp(deadline, 'R')}). "
                f"Use `/pick` in {channel.mention}."
            )
        except discord.HTTPException:
            pass  # DMs closed; the channel ping still reached them
    return True


async def close_picks(bot: Longshot, ctx: WeekContext, now: datetime, *, force: bool = False) -> bool:
    """At the deadline: record no-shows and post the slip. No bet is ever chosen for anyone."""
    row = await bot.db.week(ctx.season, ctx.week)
    if row is None or not row["announced_at"]:
        return False
    if row["closed_at"] and not force:
        return False
    if now < ctx.times.deadline_at and not force:
        return False
    channel = await bot.league_channel()
    if channel is None:
        return False

    missing = await missing_members(bot, ctx.season, ctx.week)
    for discord_id in missing:
        await bot.db.add_no_show(ctx.season, ctx.week, discord_id)
    await bot.db.update_week(
        ctx.season, ctx.week, closed_at=iso(now), deadline_at=iso(ctx.times.deadline_at), skipped=0
    )

    picks = await bot.db.week_picks(ctx.season, ctx.week)
    row = await bot.db.week(ctx.season, ctx.week)
    stake = row["stake"] or await bot.settings.stake()
    lines = [f"🔒 **Week {ctx.week} picks are closed.**"]
    if missing:
        lines.append(
            f"No pick from {render.mentions(missing)}. Commissioner: a late pick can still be entered with "
            "`/pickfor` for any game that has not kicked off."
        )
    if picks:
        lines.append("Commissioner: place the parlay, then record the real odds with `/placed`.")
    await channel.send(
        "\n".join(lines),
        embed=render.slip_embed(row, picks, stake, title=f"Week {ctx.week} slip"),
        allowed_mentions=render.PING_USERS,
    )
    return True


async def recap(bot: Longshot, season: int, week: int, *, force: bool = False) -> bool:
    """Once every leg is settled: post the result and record any payout."""
    row = await bot.db.week(season, week)
    if row is None or not row["closed_at"]:
        return False
    if row["recap_at"] and not force:
        return False
    picks = await bot.db.week_picks(season, week)
    status = parlay_status(pick["status"] for pick in picks)
    settled = picks and all(pick["status"] not in (PENDING, "review") for pick in picks)
    if picks and not settled and not force:
        return False
    now = iso(discord.utils.utcnow())
    if not picks:
        await bot.db.update_week(season, week, result=VOID, recap_at=now)
        return False

    stake = row["stake"] or await bot.settings.stake()
    hits = sum(1 for pick in picks if pick["status"] == WON)
    misses = [pick for pick in picks if pick["status"] == LOST]
    payout = None
    if status == WON:
        guess = render.estimate(picks, stake)
        payout = total_return(stake, american_to_decimal(row["placed_odds"])) if row["placed_odds"] else guess[1]
        winners = len(picks)
        headline = (
            f"# 🎉 THE WEEK {week} PARLAY HIT!\n"
            f"All {hits} live legs came in. {format_money(stake)} returns **{format_money(payout)}**"
            + (f" at {format_american(row['placed_odds'])}" if row["placed_odds"] else " (estimated; `/placed` was never run)")
            + f". Split {winners} ways, that is **{format_money(round(payout / winners, 2))}** each."
        )
    elif status == LOST:
        headline = f"**Week {week} parlay: missed.** {hits} of {len(picks)} legs hit."
        if len(misses) == 1:
            headline += f" It would have cashed without {render.mention(misses[0]['discord_id'])}'s leg."
        else:
            headline += f" {len(misses)} legs missed."
    elif status == VOID:
        headline = f"**Week {week} parlay: void.** Every leg was a push or void, so the stake comes back."
        payout = stake
    else:
        headline = f"**Week {week} parlay** (not every leg is settled yet)"

    await bot.db.update_week(season, week, result=status, payout=payout, recap_at=now)
    channel = await bot.league_channel()
    if channel is not None:
        row = await bot.db.week(season, week)
        await channel.send(
            headline, embed=render.slip_embed(row, picks, stake, title=f"Week {week} final slip"),
            allowed_mentions=render.QUIET,
        )
    return True
