from __future__ import annotations

import pytest
from pydantic import ValidationError

from scoresheet_reader.models import FoulCode, FoulEntry, FoulMarkStyle, PlayerEntry, TeamEntry

from .synthetic_fixture import synthetic_document


def test_synthetic_document_round_trips_without_information_loss() -> None:
    document = synthetic_document("round-trip")
    restored = type(document).model_validate_json(document.model_dump_json())

    assert restored == document
    assert len(restored.teams[0].players) == 12
    assert sum(player.participation == "starter" for player in restored.teams[0].players) == 5
    assert {foul.code for foul in restored.teams[0].coach_fouls} == {FoulCode.C, FoulCode.B}
    assert [marker.code for marker in restored.teams[0].coach_post_foul_markers] == [
        FoulCode.GD,
        FoulCode.F,
    ]
    assert [foul.code for foul in restored.teams[0].assistant_coach_fouls] == [
        FoulCode.D,
        FoulCode.F,
        FoulCode.F,
    ]


@pytest.mark.parametrize("jersey", ["-1", "100", "A7", "01"])
def test_invalid_jersey_number_is_rejected_at_the_schema_boundary(jersey: str) -> None:
    with pytest.raises(ValidationError):
        PlayerEntry(row=1, jersey_number=jersey)


def test_a_sixth_personal_foul_slot_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FoulEntry(slot=6, code="P")


def test_a_fourth_assistant_coach_foul_slot_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TeamEntry(side="A", assistant_coach_fouls=[FoulEntry(slot=4, code="F")])


def test_cancelled_foul_and_free_throw_number_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        FoulEntry(slot=1, code="P", free_throws=2, cancelled=True)


def test_legacy_fourth_and_fifth_coach_slots_migrate_to_the_unboxed_column() -> None:
    team = TeamEntry.model_validate(
        {
            "side": "A",
            "coach_fouls": [
                {"slot": 1, "code": "C"},
                {"slot": 4, "code": "GD"},
                {"slot": 5, "code": "F"},
            ],
        }
    )

    assert [foul.slot for foul in team.coach_fouls] == [1]
    assert [(marker.slot, marker.code) for marker in team.coach_post_foul_markers] == [
        (1, FoulCode.GD),
        (2, FoulCode.F),
    ]


def test_future_rules_can_distinguish_the_same_letter_by_circle_style() -> None:
    category_1 = FoulEntry(
        slot=1,
        code="T",
        catalog_id="player.technical_category_1",
        mark_style=FoulMarkStyle.CIRCLED,
    )
    category_2 = FoulEntry(
        slot=2,
        code="T",
        catalog_id="player.technical_category_2",
    )

    assert category_1.code == category_2.code
    assert category_1.catalog_id != category_2.catalog_id
    assert category_1.mark_style == FoulMarkStyle.CIRCLED
    assert category_2.mark_style == FoulMarkStyle.PLAIN


def test_unknown_fields_are_rejected() -> None:
    payload = synthetic_document("strict").model_dump(mode="json")
    payload["recognition"] = {"model": "not-allowed"}

    with pytest.raises(ValidationError):
        type(synthetic_document()).model_validate(payload)
