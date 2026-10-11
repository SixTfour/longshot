import pytest

from longshot.domain.names import match_name, normalize, team_key


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("D.J. Moore", "dj moore"),
        ("Marvin Harrison Jr.", "marvin harrison"),
        ("Amon-Ra St. Brown", "amon ra st brown"),
        ("Ja'Marr Chase", "jamarr chase"),
        ("Kenneth Walker III", "kenneth walker"),
        ("José Ramírez", "jose ramirez"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_exact_match_ignores_punctuation_and_suffix():
    assert match_name("DJ Moore", ["D.J. Moore", "David Moore"]) == "D.J. Moore"
    assert match_name("Marvin Harrison", ["Marvin Harrison Jr."]) == "Marvin Harrison Jr."


def test_loose_match_on_initial_and_last_name():
    assert match_name("Gabe Davis", ["Gabriel Davis", "Josh Allen"]) == "Gabriel Davis"


def test_ambiguous_loose_match_is_refused():
    assert match_name("J. Williams", ["Jameson Williams", "Javonte Williams"]) is None


def test_no_match():
    assert match_name("Hollywood Brown", ["Marquise Brown", "Travis Kelce"]) is None
    assert match_name("Jalen Hurts", []) is None


def test_team_key_maps_other_sources_to_espn():
    assert team_key("WAS") == "WSH"
    assert team_key("phi") == "PHI"
