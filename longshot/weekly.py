"""The scheduled steps of a week: announce the loser, remind, close picks, recap.

Each step first checks the parlay's row to see whether it has already run, so
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
from .domain.cards import MAIN, card_scope, card_title
from .domain.grading import LOST, PENDING, REVIEW, VOID, WON, parlay_status
from .domain.loser import lowest_scorers
from .domain.odds_math import american_to_decimal, format_american, format_money, total_return
from .domain.schedule import stamp
from .nfl import Parlay, WeekPlan
from .views import paid_view

if TYPE_CHECKING:
    from .bot import Longshot

log = logging.getLogger(__name__)


async def record_loser(bot: Longshot, season: int, week: int, team: dict[str, Any], points: float | None) -> None:
    """Store who owes the stake for a week's main parlay."""
    stake = await bot.settings.stake()
    await bot.db.ensure_parlay(season, week, MAIN)
    await bot.db.update_parlay(
        season, week, MAIN, stake=stake, loser_roster_id=team["roster_id"], loser_team=team["team_name"],
        loser_points=points, tied_roster_ids=None,
    )
    await bot.db.set_debt(season, week, team["roster_id"], team["team_name"], stake)


async def _team_text(bot: Longshot, team: dict[str, Any]) -> str:
    member = await bot.db.member_by_roster(team["roster_id"])
    return f"**{team['team_name']}** ({render.mention(member['discord_id'])})" if member else f"**{team['team_name']}**"


async def announce(bot: Longshot, plan: WeekPlan, now: datetime, *, force: bool = False) -> bool:
    """Post last week's lowest scorer and open picks for each of the week's parlays."""
    season, week = plan.season, plan.week
    rows = {parlay.card: await bot.db.ensure_parlay(*parlay.key) for parlay in plan.parlays}
    if rows[MAIN]["announced_at"] and not force:
        return False
    league_id, channel = await bot.league_id(), await bot.league_channel()
    if league_id is None or channel is None:
        return False

    stake = await bot.settings.stake()
    lines = [f"## Week {week} parlay{'s' if plan.split else ''}"]
    view = None
    pings: list[int] = []
    payer = "the lowest scorer"

    if week == 1:
        lines.append("It is Week 1, so there is no lowest scorer yet.")
    else:
        try:
            teams = {team["roster_id"]: team for team in await bot.sleeper.teams(league_id)}
            low = lowest_scorers(await bot.sleeper.matchups(league_id, week - 1))
        except SleeperError as error:
            log.warning("Could not read Week %s scores from Sleeper: %s", week - 1, error)
            return False

        tied = [teams[roster] for roster in (low[1] if low else []) if roster in teams]
        if low is None or not tied:
            lines.append(f"Sleeper has no scores for Week {week - 1}. Commissioner: set who pays with `/admin loser`.")
        elif len(tied) == 1:
            await record_loser(bot, season, week, tied[0], low[0])
            member = await bot.db.member_by_roster(tied[0]["roster_id"])
            if member:
                pings.append(member["discord_id"])
            payer = f"**{tied[0]['team_name']}**"
            target = "the Sunday and Monday parlay" if plan.split else "this week's parlay"
            lines.append(
                f"{await _team_text(bot, tied[0])} had the lowest score of Week {week - 1} with "
                f"**{low[0]:g}** points and owes **{format_money(stake)}** for {target}."
            )
            view = paid_view(season, week)
        else:
            names = ", ".join([await _team_text(bot, team) for team in tied])
            await bot.db.update_parlay(
                season, week, MAIN, stake=stake, loser_points=low[0],
                tied_roster_ids=json.dumps([team["roster_id"] for team in tied]),
            )
            lines.append(
                f"Week {week - 1}'s lowest score was a tie at **{low[0]:g}** points: {names}. "
                "Commissioner: choose who pays with `/admin loser`."
            )

    lines.append("")
    if plan.split:
        lines.append("**Two parlays this week**, one pick each:")
    any_open = False
    for parlay in plan.parlays:
        deadline = parlay.times.deadline_at
        closes = f"{stamp(deadline, 'F')} ({stamp(deadline, 'R')})"
        if now < deadline:
            any_open = True
            await bot.db.update_parlay(*parlay.key, stake=stake, announced_at=iso(now))
            if plan.split:
                funded = f"paid for by {payer if parlay.loser_pays else 'the commissioner'}"
                lines.append(
                    f"- **{parlay.title}**: {card_scope(parlay.card, True)}, {funded}. Picks close {closes}."
                )
            else:
                lines.append(f"**Picks are open** until {closes}.")
        else:
            # The bot was not running (or not set up) before this deadline.
            await bot.db.update_parlay(
                *parlay.key, stake=stake, announced_at=iso(now), closed_at=iso(now),
                deadline_at=iso(deadline), skipped=1,
            )
            which = f"The {parlay.title} pick deadline" if plan.split else "This week's pick deadline"
            lines.append(f"{'- ' if plan.split else ''}{which} has already passed, so no picks are being collected for it.")

    if any_open:
        book = await bot.settings.sportsbook_name()
        members = [member["discord_id"] for member in await bot.db.members()]
        pings.extend(members)
        lines.append(f"Find your bet on {book}, then lock it in with `/pick`.")
        if members:
            lines.append(render.mentions(members))

    await channel.send(
        "\n".join(lines),
        view=view,
        allowed_mentions=discord.AllowedMentions(users=[discord.Object(id) for id in set(pings)]),
    )
    return True


async def missing_members(bot: Longshot, season: int, week: int, card: str) -> list[int]:
    picked = {pick["discord_id"] for pick in await bot.db.parlay_picks(season, week, card)}
    return [member["discord_id"] for member in await bot.db.members() if member["discord_id"] not in picked]


async def remind(bot: Longshot, parlay: Parlay, now: datetime, *, force: bool = False) -> bool:
    """Nudge members who have not picked, in the channel and by DM."""
    row = await bot.db.parlay(*parlay.key)
    if row is None or not row["announced_at"] or row["closed_at"]:
        return False
    deadline = parlay.times.deadline_at
    sent = json.loads(row["reminders_sent"])
    due = [hours for hours, at in parlay.times.reminders.items() if at <= now < deadline and hours not in sent]
    if not due and not force:
        return False
    # If several came due while the bot was offline, one reminder covers them all.
    await bot.db.update_parlay(*parlay.key, reminders_sent=json.dumps(sorted(set(sent) | set(due))))

    missing = await missing_members(bot, *parlay.key)
    channel = await bot.league_channel()
    if not missing or channel is None:
        return False
    await channel.send(
        f"⏰ {parlay.title} picks close {stamp(deadline, 'R')}. Still waiting on {render.mentions(missing)}. "
        "Use `/pick`.",
        allowed_mentions=render.PING_USERS,
    )
    for discord_id in missing:
        try:
            user = bot.get_user(discord_id) or await bot.fetch_user(discord_id)
            await user.send(
                f"Your {parlay.title} parlay pick is due {stamp(deadline, 'F')} ({stamp(deadline, 'R')}). "
                f"Use `/pick` in {channel.mention}."
            )
        except discord.HTTPException:
            pass  # DMs closed; the channel ping still reached them
    return True


async def close_picks(bot: Longshot, parlay: Parlay, now: datetime, *, force: bool = False) -> bool:
    """At the deadline: record no-shows and post the slip. No bet is ever chosen for anyone."""
    row = await bot.db.parlay(*parlay.key)
    if row is None or not row["announced_at"]:
        return False
    if row["closed_at"] and not force:
        return False
    if now < parlay.times.deadline_at and not force:
        return False
    channel = await bot.league_channel()
    if channel is None:
        return False

    missing = await missing_members(bot, *parlay.key)
    for discord_id in missing:
        await bot.db.add_no_show(*parlay.key, discord_id)
    await bot.db.update_parlay(
        *parlay.key, closed_at=iso(now), deadline_at=iso(parlay.times.deadline_at), skipped=0
    )

    picks = await bot.db.parlay_picks(*parlay.key)
    row = await bot.db.parlay(*parlay.key)
    stake = row["stake"] or await bot.settings.stake()
    lines = [f"🔒 **{parlay.title} picks are closed.**"]
    if missing:
        lines.append(
            f"No pick from {render.mentions(missing)}. Commissioner: a late pick can still be entered with "
            "`/pickfor` for any game that has not kicked off."
        )
    if picks:
        lines.append("Commissioner: place the parlay, then record the real odds with `/placed`.")
    await channel.send(
        "\n".join(lines),
        embed=render.slip_embed(row, picks, stake, title=f"{parlay.title} slip"),
        allowed_mentions=render.PING_USERS,
    )
    return True


async def recap(bot: Longshot, season: int, week: int, card: str, *, force: bool = False) -> bool:
    """Once every leg is settled: post the result and record any payout."""
    row = await bot.db.parlay(season, week, card)
    if row is None or not row["closed_at"]:
        return False
    if row["recap_at"] and not force:
        return False
    picks = await bot.db.parlay_picks(season, week, card)
    status = parlay_status(pick["status"] for pick in picks)
    settled = picks and all(pick["status"] not in (PENDING, REVIEW) for pick in picks)
    if picks and not settled and not force:
        return False
    now = iso(discord.utils.utcnow())
    if not picks:
        await bot.db.update_parlay(season, week, card, result=VOID, recap_at=now)
        return False

    title = card_title(week, card)
    stake = row["stake"] or await bot.settings.stake()
    hits = sum(1 for pick in picks if pick["status"] == WON)
    misses = [pick for pick in picks if pick["status"] == LOST]
    payout = None
    if status == WON:
        guess = render.estimate(picks, stake)
        payout = total_return(stake, american_to_decimal(row["placed_odds"])) if row["placed_odds"] else guess[1]
        winners = len(picks)
        headline = (
            f"# 🎉 THE {title.upper()} PARLAY HIT!\n"
            f"All {hits} live legs came in. {format_money(stake)} returns **{format_money(payout)}**"
            + (f" at {format_american(row['placed_odds'])}" if row["placed_odds"] else " (estimated; `/placed` was never run)")
            + f". Split {winners} ways, that is **{format_money(round(payout / winners, 2))}** each."
        )
    elif status == LOST:
        headline = f"**{title} parlay: missed.** {hits} of {len(picks)} legs hit."
        if len(misses) == 1:
            headline += f" It would have cashed without {render.mention(misses[0]['discord_id'])}'s leg."
        else:
            headline += f" {len(misses)} legs missed."
    elif status == VOID:
        headline = f"**{title} parlay: void.** Every leg was a push or void, so the stake comes back."
        payout = stake
    else:
        headline = f"**{title} parlay** (not every leg is settled yet)"

    await bot.db.update_parlay(season, week, card, result=status, payout=payout, recap_at=now)
    channel = await bot.league_channel()
    if channel is not None:
        row = await bot.db.parlay(season, week, card)
        await channel.send(
            headline, embed=render.slip_embed(row, picks, stake, title=f"{title} final slip"),
            allowed_mentions=render.QUIET,
        )
    return True
