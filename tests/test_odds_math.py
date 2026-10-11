import pytest

from longshot.domain.odds_math import (
    american_to_decimal,
    decimal_to_american,
    format_american,
    format_money,
    parlay_decimal,
    parse_american,
    total_return,
)


@pytest.mark.parametrize("price, decimal", [(100, 2.0), (150, 2.5), (-110, 1.9090909), (-200, 1.5), (-100, 2.0)])
def test_american_to_decimal(price, decimal):
    assert american_to_decimal(price) == pytest.approx(decimal)


@pytest.mark.parametrize("price", [0, 50, -99])
def test_invalid_american_price(price):
    with pytest.raises(ValueError):
        american_to_decimal(price)


@pytest.mark.parametrize("price", [100, 105, 320, 12500, -110, -115, -240, -1000])
def test_round_trip(price):
    assert decimal_to_american(american_to_decimal(price)) == price


def test_parlay_of_two_even_money_legs_pays_three_to_one():
    assert parlay_decimal([100, 100]) == pytest.approx(4.0)
    assert decimal_to_american(parlay_decimal([100, 100])) == 300


def test_parlay_with_no_legs_is_a_refund():
    assert parlay_decimal([]) == 1.0


def test_total_return_includes_the_stake():
    assert total_return(10, parlay_decimal([-110, -110])) == 36.45


@pytest.mark.parametrize("text, price", [("+12500", 12500), ("12,500", 12500), (" -110 ", -110), ("250", 250)])
def test_parse_american(text, price):
    assert parse_american(text) == price


@pytest.mark.parametrize("text", ["abc", "", "50", "-20", "1.5"])
def test_parse_american_rejects(text):
    with pytest.raises(ValueError):
        parse_american(text)


def test_formatting():
    assert format_american(105) == "+105"
    assert format_american(-115) == "-115"
    assert format_american(125000) == "+125,000"
    assert format_money(10) == "$10"
    assert format_money(1234.5) == "$1,234.50"
