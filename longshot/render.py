"""How picks and slips are written out in Discord."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import discord

from .clients.espn import Game
from .db import parse_iso
from .domain.cards import card_title
from .domain.grading import DROPPED, LOST, PENDING, PUSH, REVIEW, VOID, WON, parlay_status
from .domain.schedule import kickoff_text
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
# One square per leg in the parlay bar. A leg still to be decided is yellow once its game is under way.
SQUARE = {WON: "🟩", LOST: "🟥", PUSH: "⬛", VOID: "⬛", REVIEW: "🟪"}
SQUARE_LIVE, SQUARE_WAITING = "🟨", "⬜"
BAR_SEGMENTS = 10
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


def bar(done: float, needed: float) -> str:
    """A ten-segment progress bar: ▰▰▰▰▰▰▱▱▱▱"""
    fraction = 1.0 if needed <= 0 else max(0.0, min(1.0, done / needed))
    filled = int(fraction * BAR_SEGMENTS + 1e-9)
    return "▰" * filled + "▱" * (BAR_SEGMENTS - filled)


def in_play(pick: Pick, game: Game | None) -> bool:
    """Whether an undecided leg's game has started."""
    if pick["status"] != PENDING:
        return False
    if game is not None:
        return game.state != "pre"
    return pick["stat_value"] is not None  # progress is only recorded once a game is under way


def _team_progress(pick: Pick, margin: float | None) -> str:
    """'PHI by 7 · covering' for a moneyline or spread; margin is from the picked team's side."""
    if margin is None:
        return ""
    team, final = pick["team_abbr"], pick["status"] != PENDING
    if margin > 0:
        score = f"{team} won by {margin:g}" if final else f"{team} by {margin:g}"
    elif margin < 0:
        score = f"{team} lost by {-margin:g}" if final else f"{team} down {-margin:g}"
    else:
        score = "tied"
    if pick["market"] != "spreads":
        return score
    cover = margin + (pick["line"] or 0)
    if final:
        return f"{score} · {'covered' if cover > 0 else 'push' if cover == 0 else 'did not cover'}"
    return f"{score} · {'covering' if cover > 0 else 'on the number' if cover == 0 else 'not covering'}"


def progress(pick: Pick, game: Game | None = None) -> str:
    """The second line of a leg on the slip: a progress bar, a score note, or when the game kicks off."""
    status, value, line = pick["status"], pick["stat_value"], pick["line"]
    if status in (PUSH, VOID, REVIEW):
        return {PUSH: "push", REVIEW: "needs a ruling: `/grade`"}.get(status) or pick["note"] or "void"
    if status == PENDING and not in_play(pick, game):
        return f"{kickoff_text(parse_iso(pick['kickoff']))} ET"
    if pick["market"] in ("h2h", "spreads"):
        return _team_progress(pick, value)
    if pick["side"] == "yes":
        return "scored" if status == WON else "no TD" if status == LOST else "no TD yet"
    if line is None:
        return ""

    # Over or under on a number: a player's stat, or a game's total points.
    value = value or 0.0
    needed = math.floor(line) + 1  # the first whole number past the line
    if pick["side"] == "over":
        # Written as on the slip: "52 / 40" for a 40+ line, "48 / 74.5" for over 74.5.
        target = f"{needed}" if pick["alt"] and line % 1 == 0.5 else f"{line:g}"
        return f"{bar(value, needed)}  {value:g} / {target}"
    # An under: the bar fills as the room runs out, so a full bar means it has gone over.
    if status == WON:
        return f"{bar(value, needed)}  finished with {value:g}"
    if value > line:
        return f"{bar(value, needed)}  {value:g} · over the line"
    spare = math.ceil(line) - 1 - value
    return f"{bar(value, needed)}  {value:g} so far · {spare:g} to spare"


def slip_block(pick: Pick, game: Game | None = None) -> str:
    """A leg on the slip: who and what on one line, its progress underneath."""
    head = f"{EMOJI[pick['status']]} {mention(pick['discord_id'])} · **{pick['label']}** ({format_american(pick['price'])})"
    detail = progress(pick, game)
    return f"{head}\n└ {detail}" if detail else head


def parlay_bar(picks: list[Pick], games: Mapping[str, Game] | None = None) -> str:
    """One square per leg, then the count: 🟩🟩🟨⬜  2 of 4 legs in"""
    squares = []
    for pick in picks:
        game = (games or {}).get(pick["espn_event_id"])
        squares.append(SQUARE.get(pick["status"]) or (SQUARE_LIVE if in_play(pick, game) else SQUARE_WAITING))
    live = [pick for pick in picks if pick["status"] not in DROPPED]
    hits = sum(1 for pick in live if pick["status"] == WON)
    return f"{''.join(squares)}  {hits} of {len(live)} legs in"


def slip_embed(
    parlay: Mapping[str, Any],
    picks: list[Pick],
    stake: float,
    *,
    title: str | None = None,
    games: Mapping[str, Game] | None = None,
) -> discord.Embed:
    """A parlay's legs, each with its progress, and the odds.

    parlay is its database row. games, by ESPN id, says which games are under
    way; without it a leg counts as in play once it has progress recorded.
    """
    status = parlay_status(pick["status"] for pick in picks)
    colour = {WON: GREEN, LOST: RED, VOID: GREY}.get(status, GOLD)
    embed = discord.Embed(title=title or f"{card_title(parlay['week'], parlay['card'])} parlay", colour=colour)
    if picks:
        blocks = [slip_block(pick, (games or {}).get(pick["espn_event_id"])) for pick in picks]
        embed.description = "\n".join([parlay_bar(picks, games), "", *blocks])
        embed.add_field(name=f"{len(picks)} legs", value=odds_summary(parlay, picks, stake), inline=False)
    else:
        embed.description = "No picks yet."
    return embed
