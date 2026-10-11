"""Turning odds-feed markets into the lines a member chooses from in /pick."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .markets import TEAM_ALTERNATES, PropMarket
from .names import match_name, normalize
from .odds_math import format_american

# Outcomes for one bookmaker, keyed by market: {"spreads": [{"name": ..., "price": ..., "point": ...}]}
Markets = Mapping[str, Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class Line:
    market: str  # the market the pick is stored and graded under (never an _alternate key)
    side: str  # 'home'/'away' for moneyline and spread, 'over'/'under', or 'yes'
    point: float | None
    price: int
    alt: bool
    text: str  # what the member sees, without the price: "PHI -2.5", "Over 34.5", "40+"

    @property
    def key(self) -> str:
        """Identifies this line within its list; sent back by Discord when it is chosen."""
        return self.side if self.point is None else f"{self.side}:{self.point:g}"

    @property
    def choice(self) -> str:
        return f"{self.text} ({format_american(self.price)})"


def bookmaker_markets(event: Mapping[str, Any] | None, bookmaker: str) -> dict[str, list[dict]]:
    """Pull one bookmaker's markets out of an Odds API event."""
    for book in (event or {}).get("bookmakers", []):
        if book.get("key") == bookmaker:
            return {market["key"]: list(market.get("outcomes", [])) for market in book.get("markets", [])}
    return {}


def _dedupe(lines: list[Line]) -> list[Line]:
    """Alternate markets usually repeat the main line; keep the main one."""
    seen: dict[str, Line] = {}
    for line in sorted(lines, key=lambda item: item.alt):
        seen.setdefault(line.key, line)
    return list(seen.values())


def _by_closeness(lines: list[Line]) -> list[Line]:
    """Main line first, then alternates nearest to it, so the 25 Discord shows are the likely ones."""
    main = next((line.point for line in lines if not line.alt and line.point is not None), None)

    def rank(line: Line):
        distance = abs(line.point - main) if main is not None and line.point is not None else 0.0
        return (line.alt, distance, line.point or 0.0, line.side != "over")

    return sorted(lines, key=rank)


def team_lines(markets: Markets, bet: str, *, team_name: str, team_abbr: str, is_home: bool) -> list[Line]:
    """Lines for a moneyline, spread or game total, from the point of view of one team."""
    side = "home" if is_home else "away"
    wanted = normalize(team_name)
    lines: list[Line] = []

    if bet == "h2h":
        for outcome in markets.get("h2h", []):
            if normalize(outcome["name"]) == wanted:
                lines.append(Line("h2h", side, None, int(outcome["price"]), False, f"{team_abbr} moneyline"))
        return lines

    for market_key, alt in ((bet, False), (TEAM_ALTERNATES[bet], True)):
        for outcome in markets.get(market_key, []):
            point = outcome.get("point")
            if point is None:
                continue
            point, price = float(point), int(outcome["price"])
            if bet == "spreads":
                if normalize(outcome["name"]) == wanted:
                    text = f"{team_abbr} PK" if point == 0 else f"{team_abbr} {point:+g}"
                    lines.append(Line("spreads", side, point, price, alt, text))
            else:
                name = outcome["name"].lower()
                if name in ("over", "under"):
                    lines.append(Line("totals", name, point, price, alt, f"{name.title()} {point:g}"))
    return _by_closeness(_dedupe(lines))


def _prop_text(side: str, point: float, alt: bool) -> str:
    # Sportsbooks show an alternate "over 39.5" as "40+".
    if alt and side == "over" and point % 1 == 0.5:
        return f"{int(point + 0.5)}+"
    return f"{side.title()} {point:g}"


def player_lines(markets: Markets, prop: PropMarket, player_name: str) -> list[Line]:
    """Main and alternate lines for one player in one prop market."""
    keys = [(prop.key, False)]
    if prop.alternate_key:
        keys.append((prop.alternate_key, True))

    described = {outcome.get("description", "") for key, _ in keys for outcome in markets.get(key, [])}
    feed_name = match_name(player_name, described - {""})
    if feed_name is None:
        return []

    lines: list[Line] = []
    for market_key, alt in keys:
        for outcome in markets.get(market_key, []):
            if outcome.get("description") != feed_name:
                continue
            name, price = outcome["name"].lower(), int(outcome["price"])
            if prop.yes_only:
                if name == "yes":
                    lines.append(Line(prop.key, "yes", None, price, False, prop.label))
            elif name in ("over", "under") and outcome.get("point") is not None:
                point = float(outcome["point"])
                lines.append(Line(prop.key, name, point, price, alt, _prop_text(name, point, alt)))

    lines = _dedupe(lines)
    return sorted(lines, key=lambda line: (line.alt, line.point or 0.0, line.side != "over"))


_OVER_UNDER = re.compile(r"^(o|over|u|under)\s*(\d+(?:\.\d+)?)$")
_OR_MORE = re.compile(r"^(\d+)\s*\+$")


def typed_line(
    bet: str, text: str | None, price: int, *, team_abbr: str = "", is_home: bool = False, prop: PropMarket | None = None
) -> Line:
    """A line typed in from an old bet slip, for /backfill, where the odds feed has nothing to offer.

    bet is 'h2h', 'spreads', 'totals' or a prop's key. Raises ValueError, saying
    what was expected, if the text cannot be read as a line for that bet.
    """
    typed = (text or "").strip().lower().replace("\u2212", "-")
    team_side = "home" if is_home else "away"

    if bet == "h2h":
        return Line("h2h", team_side, None, price, False, f"{team_abbr} moneyline")
    if prop is not None and prop.yes_only:
        return Line(prop.key, "yes", None, price, False, prop.label)

    if bet == "spreads":
        if typed in ("pk", "pick", "pickem", "pick'em", "even"):
            point = 0.0
        else:
            try:
                point = float(typed)
            except ValueError:
                point = math.nan
        if not math.isfinite(point) or abs(point) > 60:
            raise ValueError(f"For a spread, give {team_abbr or 'the team'}'s line, such as `-2.5` or `+3`.")
        text_out = f"{team_abbr} PK" if point == 0 else f"{team_abbr} {point:+g}"
        return Line("spreads", team_side, point, price, False, text_out)

    market = "totals" if bet == "totals" else prop.key
    match = _OVER_UNDER.match(typed)
    if match:
        side = "over" if match.group(1) in ("o", "over") else "under"
        point = float(match.group(2))
        return Line(market, side, point, price, False, f"{side.title()} {point:g}")
    match = _OR_MORE.match(typed)
    if match and bet != "totals":
        # "75+" on a slip is over 74.5.
        return Line(market, "over", int(match.group(1)) - 0.5, price, True, f"{int(match.group(1))}+")
    if bet == "totals":
        raise ValueError("For a game total, give the line as `over 46.5` or `under 46.5`.")
    raise ValueError("Give the line as `over 74.5`, `under 74.5` or `75+`.")


def filter_lines(lines: Sequence[Line], typed: str) -> list[Line]:
    """Narrow a list by what the member has typed so far."""
    needle = _squash(typed)
    if not needle:
        return list(lines)
    # Match the line itself first, so "40" finds "40+" and not every price containing 40.
    by_text = [line for line in lines if needle in _squash(line.text)]
    return by_text or [line for line in lines if needle in _squash(line.choice)]


def _squash(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def find_line(lines: Sequence[Line], value: str) -> Line | None:
    """Resolve what Discord sent for the line field.

    Normally that is a Line.key from the list. If the member typed text without
    choosing, accept it only when it is exactly one line's wording, so a
    half-typed entry can never become a bet.
    """
    for line in lines:
        if line.key == value:
            return line
    typed = _squash(value)
    exact = [line for line in lines if typed in (_squash(line.text), _squash(line.choice))]
    return exact[0] if len(exact) == 1 else None


def pick_label(line: Line, *, game: str, player: str | None = None, noun: str | None = None) -> str:
    """How a pick reads on the slip, without its price."""
    if player is not None:
        if line.side == "yes":
            return f"{player} {noun}"
        text = line.text if line.text.endswith("+") else line.text.lower()
        return f"{player} {text} {noun}"
    if line.market == "totals":
        return f"{game} {line.text.lower()} points"
    return line.text


def conflict(new: Mapping[str, Any], existing: Mapping[str, Any]) -> str | None:
    """Whether two picks are the 'same' leg, direct 'opposite's, or unrelated (None)."""
    if new["espn_event_id"] != existing["espn_event_id"] or new["market"] != existing["market"]:
        return None
    if new.get("player_id") != existing.get("player_id"):
        return None

    same_side = new["side"] == existing["side"]
    if new["market"] == "h2h":
        return "same" if same_side else "opposite"
    if new["market"] == "spreads":
        if same_side:
            return "same" if new["line"] == existing["line"] else None
        return "opposite" if new["line"] == -existing["line"] else None
    if new["line"] != existing["line"]:
        return None
    return "same" if same_side else "opposite"
