"""The injury exemption, checked against real play-by-play.

Commanders at 49ers (ESPN event 401873008): Terry McLaurin and Mike Evans both
left injured and did not return. Buccaneers at Cowboys (401872980): CeeDee Lamb
and Sean Revel were hurt and came back.
"""

import pytest

from longshot.clients.espn import parse_injuries
from longshot.domain.grading import HOLD_NOTE, INJURED_NOTE, LOST, PENDING, PUSH, VOID, WON, Grade, apply_injury
from longshot.domain.injuries import (
    INJURED,
    LEFT_INJURED,
    NOT_INJURED,
    RETURNED,
    InjuryNote,
    injury_status,
    parse_notes,
)


@pytest.fixture
def plays(fixture):
    games = fixture("espn_injury_plays.json")
    return {event: parse_injuries(game) for event, game in games.items()}


def test_players_who_left_injured(plays):
    notes = plays["401873008"]
    # The gamebook writes Washington as WAS; ESPN calls it WSH.
    assert injury_status(notes, "Terry McLaurin", ["WSH"]) == LEFT_INJURED
    assert injury_status(notes, "Mike Evans", ["SF"]) == LEFT_INJURED
    assert injury_status(notes, "Mike Evans", ["WSH", "SF"]) == LEFT_INJURED  # team not known: either side
    assert injury_status(notes, "Mike Evans", ["WSH"]) is None  # not on that team


def test_players_who_came_back(plays):
    notes = plays["401872980"]
    assert injury_status(notes, "Sean Revel", ["DAL"]) == RETURNED  # hurt in the 3rd, back in the 4th
    assert injury_status(notes, "CeeDee Lamb", ["DAL"]) == RETURNED  # only the return is logged
    assert injury_status(notes, "Bucky Irving", ["TB"]) is None


def test_both_kinds_of_note_are_read_in_order():
    notes = parse_notes([
        "B.Irving up the middle to TB 31 for 1 yard (Q.Williams; A.Speed). DAL-J.Barham was injured during the play.",
        "(Shotgun) pass incomplete. ** Injury Update: DAL-J.Barham has returned to the game. PENALTY on DAL-X.Y, Holding.",
        "Kneel. ATL-Av.Terrell was injured during the play.",
    ])
    assert notes == [
        InjuryNote("DAL", "J.Barham", False),
        InjuryNote("DAL", "J.Barham", True),
        InjuryNote("ATL", "Av.Terrell", False),
    ]
    assert injury_status(notes, "Jalen Barham", ["DAL"]) == RETURNED


@pytest.mark.parametrize(
    ("written", "name", "matches"),
    [
        ("T.Hockenson", "T.J. Hockenson", True),
        ("A.St. Brown", "Amon-Ra St. Brown", True),
        ("M.Harrison", "Marvin Harrison Jr.", True),
        ("K.Walker", "Kenneth Walker III", True),
        ("C.Gardner-Johnson", "Chauncey Gardner-Johnson", True),
        ("Av.Terrell", "Avieon Terrell", True),
        # Two Joneses on one team: the gamebook adds letters to tell them apart.
        ("Dr.Jones", "Dre'Mont Jones", True),
        ("Dr.Jones", "DaQuan Jones", False),
        ("D.Smith", "DeVonta Smith", True),
        ("D.Smith", "Dallas Goedert", False),
        ("D.Smith", "Smith", False),
    ],
)
def test_gamebook_names(written, name, matches):
    status = injury_status([InjuryNote("PHI", written, False)], name, ["PHI"])
    assert (status == LEFT_INJURED) == matches


LOSS = Grade(LOST, 40)


def test_a_miss_by_a_player_who_left_injured_is_void():
    assert apply_injury(LOSS, "over", LEFT_INJURED, None, False) == Grade(VOID, 40, INJURED_NOTE)
    assert apply_injury(Grade(LOST, 0), "yes", LEFT_INJURED, None, False).status == VOID  # anytime TD


def test_a_miss_by_a_player_who_came_back_waits_then_counts():
    assert apply_injury(LOSS, "over", RETURNED, None, hold_over=False) == Grade(PENDING, 40, HOLD_NOTE)
    assert apply_injury(LOSS, "over", RETURNED, None, hold_over=True) == LOSS


def test_no_injury_means_a_miss_counts_at_once():
    assert apply_injury(LOSS, "over", None, None, False) == LOSS


def test_hits_and_pushes_stand():
    for grade in (Grade(WON, 80), Grade(PUSH, 75), Grade(PENDING, 30)):
        assert apply_injury(grade, "over", LEFT_INJURED, INJURED, False) == grade


def test_an_under_is_never_voided():
    # An under only misses by going over, which an injury does not cause.
    assert apply_injury(LOSS, "under", LEFT_INJURED, None, False) == LOSS
    assert apply_injury(LOSS, "under", None, INJURED, False) == LOSS


def test_the_commissioner_outranks_espn():
    assert apply_injury(LOSS, "over", None, INJURED, False).status == VOID
    assert apply_injury(LOSS, "over", RETURNED, INJURED, False).status == VOID
    assert apply_injury(LOSS, "over", LEFT_INJURED, NOT_INJURED, False) == LOSS
    assert apply_injury(LOSS, "over", RETURNED, NOT_INJURED, False) == LOSS  # no hold either
