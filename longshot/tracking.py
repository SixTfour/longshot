"""Live leg tracking: grade legs as games play out, and post an update after each slate.

A parlay is dead as soon as one leg misses. That miss is announced, and after
it the parlay goes quiet: its other legs are still graded, silently, and one
final update with every leg's status is posted when the last game in its
window ends (see weekly.recap).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import render
from .clients.espn import EspnError, Game
from .db import parse_iso
from .domain.cards import card_title, split_week
from .domain.grading import (
    DROPPED,
    LOST,
    PENDING,
    PUSH,
    REVIEW,
    VOID,
    WON,
    Grade,
    grade_prop,
    grade_team_bet,
)
from .domain.markets import PROP_MARKETS
from .domain.schedule import stamp
from .domain.slates import Slate, build_slates

if TYPE_CHECKING:
    from .bot import Longshot

log = logging.getLogger(__name__)

Pick = dict[str, Any]
UNSETTLED = (PENDING, REVIEW)

_HEADLINE = {WON: "Leg hit", LOST: "Leg missed", PUSH: "Leg dropped", VOID: "Leg dropped", REVIEW: "Needs a ruling"}


async def weeks_to_poll(bot: Longshot, now: datetime) -> list[tuple[int, int]]:
    """Weeks with something live.

    That is an unsettled leg whose game has started, a started slate not yet
    reported, or a dead parlay waiting on a game in progress for its final
    update. Kickoff times come from the schedule, not from the pick, so a game
    the NFL moves is followed.
    """
    wanted: set[tuple[int, int]] = set()
    for row in await bot.db.parlays():
        if row["recap_at"]:
            continue
        season, week, card = row["season"], row["week"], row["card"]
        picks = await bot.db.parlay_picks(season, week, card)
        if not picks:
            continue
        schedule = await bot.nfl.games(season, week)
        games = {game.espn_id: game for game in schedule}

        for pick in picks:
            game = games.get(pick["espn_event_id"])
            if game and game.time_set and parse_iso(pick["kickoff"]) != game.kickoff:
                await bot.db.set_kickoff(pick["id"], game.kickoff)
            if game and pick["status"] == PENDING and game.kickoff <= now:
                wanted.add((season, week))

        card_games = split_week(schedule).get(card, [])
        posted = set(json.loads(row["slates_posted"]))
        with_legs = {pick["espn_event_id"] for pick in picks}
        for slate in build_slates(card_games):
            if slate.key not in posted and slate.start <= now and with_legs & set(slate.game_ids):
                wanted.add((season, week))

        dead = any(pick["status"] == LOST for pick in picks)
        if dead and any(game.time_set and game.kickoff <= now and not game.over for game in card_games):
            wanted.add((season, week))
    return sorted(wanted)


async def track(bot: Longshot, now: datetime, *, quiet: bool = False) -> None:
    """Grade and report everything live. quiet grades without posting, outside the regular season."""
    for season, week in await weeks_to_poll(bot, now):
        try:
            await track_week(bot, season, week, quiet=quiet)
        except EspnError as error:
            log.warning("Could not track Week %s: %s", week, error)


async def _grade_game(bot: Longshot, game: Game, legs: list[Pick]) -> list[tuple[Pick, Grade]]:
    """Grade every pending leg on one game from live or final data."""
    if game.called_off:
        note = "The game was postponed or cancelled. Settle with /grade."
        return [(leg, Grade(REVIEW, note=note)) for leg in legs]

    box = await bot.espn.boxscore(game.espn_id) if any(leg["player_id"] for leg in legs) else {}
    played: dict[str, bool] | None = None
    results = []
    for leg in legs:
        if leg["player_id"] is None:
            grade = grade_team_bet(leg["market"], leg["side"], leg["line"], game.home_score, game.away_score, game.final)
        else:
            line = box.get(leg["player_id"])
            was_active = None
            if line is None and game.final:
                # No stat line: find out whether he sat out or just never touched the ball.
                if played is None:
                    try:
                        played = await bot.espn.played(game)
                    except EspnError as error:
                        log.warning("Could not read the game roster for %s: %s", game.label, error)
                        played = {}
                if played:
                    # Not on the game-day roster at all also means he did not play.
                    was_active = played.get(leg["player_id"], False)
            grade = grade_prop(
                PROP_MARKETS[leg["market"]], leg["side"], leg["line"], line.stats if line else None,
                game.final, was_active,
            )
        results.append((leg, grade))
    return results


async def track_week(bot: Longshot, season: int, week: int, *, quiet: bool = False) -> None:
    """Fetch the week's live scores once, then grade and report each of its parlays."""
    schedule = await bot.nfl.games(season, week, fresh=True)
    games = {game.espn_id: game for game in schedule}
    card_games = split_week(schedule)

    for row in await bot.db.parlays(season, week):
        card = row["card"]
        by_game: dict[str, list[Pick]] = {}
        for pick in await bot.db.parlay_picks(season, week, card):
            if pick["status"] == PENDING:
                by_game.setdefault(pick["espn_event_id"], []).append(pick)

        settled: list[int] = []
        for event_id, legs in by_game.items():
            game = games.get(event_id)
            if game is None or (game.state == "pre" and not game.called_off):
                continue
            for leg, grade in await _grade_game(bot, game, legs):
                if grade.status == PENDING:
                    if grade.value != leg["stat_value"]:
                        await bot.db.set_progress(leg["id"], grade.value)
                else:
                    await bot.db.grade_pick(leg["id"], grade.status, grade.value, grade.note, "auto")
                    settled.append(leg["id"])

        await announce_grades(bot, season, week, card, settled, quiet=quiet)
        await post_slates(bot, season, week, card, card_games.get(card, []), quiet=quiet)


def _first_miss(lost: list[Pick]) -> Pick:
    return min(lost, key=lambda pick: (pick["graded_at"] or "", pick["kickoff"], pick["id"]))


async def announce_grades(
    bot: Longshot, season: int, week: int, card: str, pick_ids: list[int], *, quiet: bool = False
) -> None:
    """Post newly settled legs while the parlay is alive, and announce the miss that kills it.

    Called after legs are graded, by the tracker or by the commissioner's /grade.
    quiet keeps the records up to date without posting or messaging anyone.
    """
    row = await bot.db.parlay(season, week, card)
    if row is None:
        return
    channel = None if quiet else await bot.league_channel()
    title = card_title(week, card)
    picks = {pick["id"]: pick for pick in await bot.db.parlay_picks(season, week, card)}
    fresh = [picks[pick_id] for pick_id in pick_ids if pick_id in picks]
    lost = [pick for pick in picks.values() if pick["status"] == LOST]
    was_dead = row["killer_pick_id"] is not None

    if was_dead and lost:
        # Dead before and still dead: nothing goes to the channel.
        killer = picks.get(row["killer_pick_id"])
        if killer is None or killer["status"] != LOST:
            await bot.db.update_parlay(season, week, card, killer_pick_id=_first_miss(lost)["id"])
        unruled = [pick for pick in fresh if pick["status"] == REVIEW]
        if unruled and not quiet:
            # The parlay is over, but the leg still needs settling for the stats.
            legs = "; ".join(f"{render.mention(pick['discord_id'])}: {render.leg(pick)}" for pick in unruled)
            await bot.tell_commissioner(
                f"The {title} parlay is already dead, but I could not grade a leg. Settle it with `/grade` so "
                f"the stats are right: {legs}"
            )
        return

    lines = []
    for pick in fresh:
        note = render.outcome(pick)
        tail = f" · *{note}*" if note and pick["status"] != REVIEW else ""
        lines.append(
            f"{render.EMOJI[pick['status']]} **{_HEADLINE[pick['status']]}** · "
            f"{render.mention(pick['discord_id'])} · {render.leg(pick)}{tail}"
        )
    if any(pick["status"] == REVIEW for pick in fresh):
        lines.append("Commissioner: settle the ❓ legs with `/grade`.")
    if was_dead:
        # The miss that killed it was overturned and no other leg has missed.
        await bot.db.update_parlay(season, week, card, killer_pick_id=None)
        lines.append(f"🟢 **The {title} parlay is alive again.**")
    if lines and channel is not None:
        await channel.send("\n".join(lines), allowed_mentions=render.QUIET)

    if lost:
        first = _first_miss(lost)
        await bot.db.update_parlay(season, week, card, killer_pick_id=first["id"])
        if channel is not None:
            await channel.send(
                f"💀 **The {title} parlay is dead.** {render.mention(first['discord_id'])}'s leg missed: "
                f"{render.leg(first)}.",
                allowed_mentions=render.PING_USERS,
            )


def _standing(picks: list[Pick]) -> str:
    """Where a live parlay stands. Only ever shown while no leg has missed."""
    live = [pick for pick in picks if pick["status"] not in DROPPED]
    hits = sum(1 for pick in live if pick["status"] == WON)
    to_go = sum(1 for pick in live if pick["status"] in UNSETTLED)
    return f"🟢 **Parlay: alive.** {hits} of {len(live)} legs in, {to_go} to go."


def _up_next(picks: list[Pick], slates: list[Slate], after: Slate) -> str | None:
    for slate in slates:
        if slate.start <= after.start:
            continue
        waiting = [pick for pick in picks if pick["espn_event_id"] in slate.game_ids and pick["status"] in UNSETTLED]
        if waiting:
            legs = "leg" if len(waiting) == 1 else "legs"
            who = render.mentions(pick["discord_id"] for pick in waiting)
            return f"**Next:** {slate.name}, {stamp(slate.start, 'f')} · {len(waiting)} {legs}: {who}"
    return None


async def post_slates(
    bot: Longshot, season: int, week: int, card: str, games: list[Game], *, quiet: bool = False
) -> None:
    """After a slate's last game ends, post how its legs did and where the parlay stands.

    games are the parlay's own games, with live state. Nothing is posted for a
    slate with no legs, for the parlay's last slate (the recap covers it), or
    once the parlay is dead.
    """
    row = await bot.db.parlay(season, week, card)
    if row is None:
        return
    posted = json.loads(row["slates_posted"])
    by_id = {game.espn_id: game for game in games}
    slates = build_slates(games)
    picks = await bot.db.parlay_picks(season, week, card)
    channel = None if quiet else await bot.league_channel()
    changed = False

    for slate in slates:
        if slate.key in posted or not all(by_id[game_id].over for game_id in slate.game_ids):
            continue
        posted.append(slate.key)
        changed = True
        legs = [pick for pick in picks if pick["espn_event_id"] in slate.game_ids]
        still_open = any(pick["status"] in UNSETTLED for pick in picks)
        dead = any(pick["status"] == LOST for pick in picks)
        if not legs or not still_open or dead or channel is None:
            continue
        lines = [f"### {slate.name} slate · {card_title(week, card)}"]
        lines += [render.slip_line(pick) for pick in legs]
        lines.append(_standing(picks))
        up_next = _up_next(picks, slates, slate)
        if up_next:
            lines.append(up_next)
        await channel.send("\n".join(lines), allowed_mentions=render.QUIET)

    if changed:
        await bot.db.update_parlay(season, week, card, slates_posted=json.dumps(posted))
