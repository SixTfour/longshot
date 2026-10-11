"""American odds arithmetic."""

from __future__ import annotations

from collections.abc import Iterable


def american_to_decimal(price: int) -> float:
    if price == 0 or -100 < price < 100:
        raise ValueError(f"{price} is not a valid American price")
    return 1 + (price / 100 if price > 0 else 100 / -price)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1:
        raise ValueError("decimal odds must be greater than 1")
    if decimal >= 2:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def parlay_decimal(prices: Iterable[int]) -> float:
    """Combined decimal odds, treating the legs as independent. 1.0 if there are no legs."""
    combined = 1.0
    for price in prices:
        combined *= american_to_decimal(price)
    return combined


def total_return(stake: float, decimal: float) -> float:
    """What the sportsbook pays back on a win, stake included."""
    return round(stake * decimal, 2)


def format_american(price: int) -> str:
    return f"+{price:,}" if price > 0 else f"{price:,}"


def parse_american(text: str) -> int:
    """Read a price as typed by a person: '+12500', '12,500', '-110'."""
    cleaned = text.strip().replace(",", "").replace(" ", "")
    try:
        price = int(cleaned.lstrip("+")) if not cleaned.startswith("-") else int(cleaned)
    except ValueError:
        raise ValueError(f"'{text}' is not a price. Use American odds such as +12500 or -110.") from None
    if -100 < price < 100:
        raise ValueError("American odds are never between -100 and +100.")
    return price


def format_money(amount: float) -> str:
    return f"${amount:,.2f}".removesuffix(".00")
