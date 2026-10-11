"""How picks and slips are written out in Discord."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import discord

from .domain.cards import card_title
from .domain.grading import DROPPED, LOST, PENDING, PUSH, REVIEW, VOID, WON, parlay_status
from .domain.odds_math import (
    american_to_decimal,
    decimal_to_american,
    format_american,
    format_money,
    parlay_decimal,
    total_return,
)

Pick = Mapping[str, Any]

EMOJI = {PENDING: "⏳", WON: "✅", LOST: "❌", PUSH: "➖", VOID: "➖", REVIEW: "❓"}
QUIET = discord.AllowedMentions.none()
PING_USERS = discord.AllowedMentions(users=True, everyone=False, roles=False)

GREEN, RED, GOLD, GREY = 0x2ECC71, 0xE74C3C, 0xF1C40F, 0x95A5A6


def mention(discord_id: int) -> str:
    return f"<@{discord_id}>"


def mentions(discord_ids: Iterable[int]) -> str:
    return " ".join(mention(discord_id) for discord_id in discord_ids)


def leg(pick: Pick) -> str:
    """'Jalen Hurts 40+ rushing yards (+105)'"""
    return f"{pick['label']} ({format_american(pick['price'])})"


def outcome(pick: Pick) -> str:
    """A short note on where a leg stands, or '' if there is nothing to add."""
    status, value = pick["status"], pick["stat_value"]
    is_stat = pick["player_id"] is not None and pick["side"] != "yes"
    if status == VOID:
        return pick["note"] or "void"
    if status == PUSH:
        return "push"
    if status == REVIEW:
        return "needs a ruling"
    if value is None:
        return ""
    if pick["market"] == "totals":
        return f"{value:g} points" if status != PENDING else f"{value:g} points so far"
    if is_stat:
        return f"finished with {value:g}" if status != PENDING else f"{value:g} so far"
    return ""


def slip_line(pick: Pick) -> str:
    note = outcome(pick)
    tail = f" · *{note}*" if note else ""
    return f"{EMOJI[pick['status']]} {mention(pick['discord_id'])} · **{pick['label']}** ({format_american(pick['price'])}){tail}"


def estimate(picks: Iterable[Pick], stake: float) -> tuple[int, float] | None:
    """Combined American odds and total return for the legs still in the parlay."""
    prices = [pick["price"] for pick in picks if pick["status"] not in DROPPED]
    if not prices:
        return None
    decimal = parlay_decimal(prices)
    return decimal_to_american(decimal), total_return(stake, decimal)


def odds_summary(week: Mapping[str, Any], picks: list[Pick], stake: float) -> str:
    if week.get("placed_odds"):
        price = week["placed_odds"]
        pays = total_return(stake, american_to_decimal(price))
        return f"Placed at **{format_american(price)}** · {format_money(stake)} returns **{format_money(pays)}**"
    guess = estimate(picks, stake)
    if guess is None:
        return "No legs yet."
    price, pays = guess
    return (
        f"Estimated odds **{format_american(price)}** · {format_money(stake)} would return about "
        f"**{format_money(pays)}**\n*An estimate from each leg's price. The sportsbook's parlay price can differ, "
        "especially with two legs from one game.*"
    )


def slip_embed(parlay: Mapping[str, Any], picks: list[Pick], stake: float, *, title: str | None = None) -> discord.Embed:
    """A parlay's legs and odds. parlay is its database row."""
    status = parlay_status(pick["status"] for pick in picks)
    colour = {WON: GREEN, LOST: RED, VOID: GREY}.get(status, GOLD)
    embed = discord.Embed(title=title or f"{card_title(parlay['week'], parlay['card'])} parlay", colour=colour)
    if picks:
        embed.description = "\n".join(slip_line(pick) for pick in picks)
        embed.add_field(name=f"{len(picks)} legs", value=odds_summary(parlay, picks, stake), inline=False)
    else:
        embed.description = "No picks yet."
    return embed
