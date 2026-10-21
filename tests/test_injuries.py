"""Early Exit, checked against real play-by-play.

Commanders at 49ers (ESPN event 401873008): Terry McLaurin and Mike Evans left
injured and did not return. Buccaneers at Cowboys
(401872980): CeeDee Lamb and Sean Revel were hurt and came back.
"""

import pytest

from longshot.clients.espn import parse_injuries
from longshot.domain.grading import INJURED_NOTE, LOST, PENDING, PUSH, VOID, WON, Grade, apply_injury
from longshot.domain.injuries import EARLY_EXIT, INJURED, NOT_INJURED, InjuryNote, injury_status, parse_notes


@pytest.fixture
def plays(fixture):
    games = fixture("espn_injury_plays.json")
    return {event: parse_injuries(game) for event, game in games.items()}


def test_early_exits(plays):
    notes = plays["401873008"]
    # The gamebook writes Washington as WAS; ESPN calls it WSH.
    assert injury_status(notes, "Terry McLaurin", ["WSH"]) == EARLY_EXIT
    assert injury_status(notes, "Mike Evans", ["SF"]) == EARLY_EXIT
    assert injury_status(notes, "Mike Evans", ["WSH", "SF"]) == EARLY_EXIT  # team not known: either side
    assert injury_status(notes, "Mike Evans", ["WSH"]) is None  # not on that team


def test_players_who_came_back_are_not_protected(plays):
    notes = plays["401872980"]
    assert injury_status(notes, "Sean Revel", ["DAL"]) is None  # hurt in the 3rd, back in the 4th
    assert injury_status(notes, "CeeDee Lamb", ["DAL"]) is None  # only the return is logged
    assert injury_status(notes, "Bucky Irving", ["TB"]) is None


def test_the_quarter_is_read_with_each_note(plays):
    revel = [note for note in plays["401872980"] if note.name == "S.Revel"]
    assert [(note.quarter, note.returned) for note in revel] == [(3, False), (4, True)]


@pytest.mark.parametrize(
    ("notes", "protected"),
    [
        ([(1, False)], True),
        ([(2, False)], True),
        ([(3, False)], True),  # the second half counts too
        ([(5, False)], True),  # and overtime
        ([(1, False), (2, True)], False),  # came back
        ([(2, False), (3, True)], False),  # came back after halftime: Tucker Kraft, Week 6
        ([(1, False), (2, True), (3, False)], True),  # came back, then left again for good
        ([(None, False)], True),
    ],
)
def test_an_exit_counts_only_without_a_return(notes, protected):
    written = [InjuryNote("GB", "T.Kraft", returned, quarter) for quarter, returned in notes]
    assert (injury_status(written, "Tucker Kraft", ["GB"]) == EARLY_EXIT) == protected


def test_both_kinds_of_note_are_read_in_order():
    notes = parse_notes([
        (4, "B.Irving up the middle to TB 31 for 1 yard (Q.Williams; A.Speed). DAL-J.Barham was injured during the play."),
        (4, "(Shotgun) pass incomplete. ** Injury Update: DAL-J.Barham has returned to the game. PENALTY on DAL-X.Y."),
        (1, "Kneel. ATL-Av.Terrell was injured during the play."),
    ])
    assert notes == [
        InjuryNote("DAL", "J.Barham", False, 4),
        InjuryNote("DAL", "J.Barham", True, 4),
        InjuryNote("ATL", "Av.Terrell", False, 1),
    ]


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
    status = injury_status([InjuryNote("PHI", written, False, 1)], name, ["PHI"])
    assert (status == EARLY_EXIT) == matches


LOSS = Grade(LOST, 40)


def test_a_miss_after_an_early_exit_is_void():
    assert apply_injury(LOSS, "over", EARLY_EXIT, None) == Grade(VOID, 40, INJURED_NOTE)
    assert apply_injury(Grade(LOST, 0), "yes", EARLY_EXIT, None).status == VOID  # anytime TD


def test_with_no_early_exit_a_miss_counts():
    assert apply_injury(LOSS, "over", None, None) == LOSS


def test_results_decided_before_the_exit_stand():
    for grade in (Grade(WON, 80), Grade(PUSH, 75), Grade(PENDING, 30)):
        assert apply_injury(grade, "over", EARLY_EXIT, INJURED) == grade


def test_an_under_is_never_voided():
    # An under only misses by going over, which an exit does not cause.
    assert apply_injury(LOSS, "under", EARLY_EXIT, None) == LOSS
    assert apply_injury(LOSS, "under", None, INJURED) == LOSS


def test_the_commissioner_outranks_espn():
    assert apply_injury(LOSS, "over", None, INJURED).status == VOID
    assert apply_injury(LOSS, "over", EARLY_EXIT, NOT_INJURED) == LOSS
