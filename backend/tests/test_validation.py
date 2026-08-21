from __future__ import annotations

import json

from scoresheet_reader.models import (
    FoulCode,
    FoulEntry,
    GamePriorSnapshot,
    PriorTeam,
    RecognitionDocumentState,
    RecognitionIssue,
)
from scoresheet_reader.settings import REPOSITORY_ROOT
from scoresheet_reader.validation import validate_document

from .synthetic_fixture import synthetic_document


def codes(document) -> set[str]:
    return {issue.code for issue in validate_document(document).issues}


def test_full_symbol_fixture_is_deterministically_valid() -> None:
    report = validate_document(synthetic_document())

    assert report.status == "valid"
    assert report.issues == []


def test_foul_validation_reads_the_selected_rule_profile_catalog(tmp_path) -> None:
    profiles = json.loads(
        (REPOSITORY_ROOT / "shared" / "rule_profiles.json").read_text(encoding="utf-8")
    )
    profiles["fiba_2024"]["foul_markings"] = [
        marking for marking in profiles["fiba_2024"]["foul_markings"] if marking["code"] != "P"
    ]
    profile_path = tmp_path / "rule_profiles.json"
    profile_path.write_text(json.dumps(profiles), encoding="utf-8")

    report = validate_document(synthetic_document(), profile_path)

    assert "FOUL_MARKING_NOT_IN_RULE_PROFILE" in {issue.code for issue in report.issues}


def test_foul_validation_uses_the_subject_specific_rule_catalog() -> None:
    document = synthetic_document()
    document.teams[0].players[0].fouls = [FoulEntry(slot=1, code="C")]
    document.teams[0].coach_fouls = [FoulEntry(slot=1, code="P")]
    document.teams[0].coach_post_foul_markers = [FoulEntry(slot=1, code="P")]

    issues = [
        issue
        for issue in validate_document(document).issues
        if issue.code == "FOUL_MARKING_NOT_IN_RULE_PROFILE"
    ]

    assert {issue.paths[0] for issue in issues} == {
        "/teams/0/players/0/fouls/0",
        "/teams/0/coach_fouls/0",
        "/teams/0/coach_post_foul_markers/0",
    }


def test_non_incrementing_running_score_is_an_error() -> None:
    document = synthetic_document()
    document.score_events[2].cumulative_score = 4

    assert "SCORE_SEQUENCE_GAP" in codes(document)


def test_invalid_or_unresolved_score_points_are_reported_defensively() -> None:
    invalid = synthetic_document()
    invalid.score_events[0].points = 4
    invalid.score_events[0].mark = None
    invalid_codes = codes(invalid)
    assert "INVALID_SCORE_POINTS" in invalid_codes
    assert "SCORE_SEQUENCE_GAP" in invalid_codes

    unresolved = synthetic_document()
    unresolved.score_events[0].points = None
    unresolved.score_events[0].mark = None
    report = validate_document(unresolved)
    assert "UNRESOLVED_SCORE_POINTS" in {issue.code for issue in report.issues}
    assert report.status == "needs_review"


def test_mark_must_match_one_two_or_three_point_semantics() -> None:
    document = synthetic_document()
    document.score_events[0].mark = "filled_dot"

    assert "SCORE_MARK_DELTA_MISMATCH" in codes(document)


def test_period_running_score_and_written_period_score_are_cross_checked() -> None:
    document = synthetic_document()
    document.stated_period_scores[0].team_a = 7

    result = codes(document)
    assert "PERIOD_SCORE_MISMATCH" in result
    assert "PERIOD_SUM_MISMATCH" in result


def test_written_period_with_no_running_score_events_is_an_error() -> None:
    document = synthetic_document()
    document.score_events = [event for event in document.score_events if event.period != 4]
    for sequence, event in enumerate(document.score_events, start=1):
        event.sequence = sequence

    report = validate_document(document)
    mismatch = next(issue for issue in report.issues if issue.code == "PERIOD_SCORE_MISMATCH")
    assert mismatch.severity == "error"
    assert mismatch.observed == {"team_a": 5, "team_b": 6}
    assert mismatch.expected == {"team_a": 0, "team_b": 0}


def test_missing_written_period_score_is_an_error() -> None:
    document = synthetic_document()
    document.stated_period_scores = [
        score for score in document.stated_period_scores if score.period != 4
    ]

    report = validate_document(document)
    missing = next(issue for issue in report.issues if issue.code == "MISSING_PERIOD_SCORE")
    assert missing.severity == "error"


def test_duplicate_written_period_score_is_an_error() -> None:
    document = synthetic_document()
    document.stated_period_scores.append(document.stated_period_scores[0].model_copy())

    duplicate = next(
        issue
        for issue in validate_document(document).issues
        if issue.code == "DUPLICATE_PERIOD_SCORE"
    )

    assert duplicate.severity == "error"
    assert duplicate.paths == ["/stated_period_scores/0", "/stated_period_scores/4"]


def test_running_score_periods_cannot_move_backwards() -> None:
    document = synthetic_document()
    document.score_events[6].period = 1

    report = validate_document(document)
    ordering = next(issue for issue in report.issues if issue.code == "SCORE_PERIOD_ORDER")
    assert ordering.severity == "error"
    assert ordering.paths[0] == "/score_events/6/period"


def test_score_event_sequence_must_be_contiguous() -> None:
    document = synthetic_document()
    document.score_events[-1].sequence += 1

    report = validate_document(document)
    ordering = next(issue for issue in report.issues if issue.code == "SCORE_EVENT_SEQUENCE_GAP")
    assert ordering.severity == "error"


def test_final_score_is_checked_against_both_event_total_and_period_sum() -> None:
    document = synthetic_document()
    document.final_score.team_a = 20

    result = codes(document)
    assert "FINAL_SCORE_MISMATCH" in result
    assert "PERIOD_SUM_MISMATCH" in result


def test_winner_must_match_final_score() -> None:
    document = synthetic_document()
    document.final_score.winner_name = document.teams[1].name

    assert "WINNER_MISMATCH" in codes(document)


def test_winner_uses_the_game_prior_standard_name() -> None:
    document = synthetic_document()
    document.game_prior = GamePriorSnapshot(
        game_id="game",
        competition="测试杯",
        division="男甲",
        date="2026-08-19",
        scheduled_time="14:00",
        venue="测试球馆",
        team_a=PriorTeam(team_id="a", name="甲队标准名", player_names=[]),
        team_b=PriorTeam(team_id="b", name="乙队标准名", player_names=[]),
        source_hash="hash",
    )
    document.final_score.winner_name = document.teams[0].name

    issue = next(
        item for item in validate_document(document).issues if item.code == "WINNER_MISMATCH"
    )
    assert issue.expected == "甲队标准名"


def test_scorer_must_exist_in_the_corresponding_roster() -> None:
    document = synthetic_document()
    document.score_events[0].scorer_jersey = "99"

    report = validate_document(document)
    scorer = next(issue for issue in report.issues if issue.code == "UNKNOWN_SCORER")
    assert scorer.severity == "error"


def test_scorer_number_is_mandatory() -> None:
    document = synthetic_document()
    document.score_events[0].scorer_jersey = ""

    result = codes(document)
    assert "MISSING_SCORER" in result
    assert "UNKNOWN_SCORER" not in result


def test_duplicate_roster_numbers_are_an_error() -> None:
    document = synthetic_document()
    document.teams[0].players[1].jersey_number = "4"

    assert "DUPLICATE_JERSEY" in codes(document)


def test_validator_defensively_reports_more_than_five_counted_fouls() -> None:
    document = synthetic_document()
    player = document.teams[0].players[0]
    player.fouls = [
        FoulEntry.model_construct(
            slot=slot, code=FoulCode.P, free_throws=None, cancelled=False, period=1
        )
        for slot in range(1, 7)
    ]

    assert "FOUL_LIMIT_EXCEEDED" in codes(document)


def test_team_foul_boxes_are_not_derived_from_personal_foul_counts() -> None:
    document = synthetic_document()
    document.teams[0].team_fouls[0].count = 4

    assert "TEAM_FOUL_MISMATCH" not in codes(document)


def test_tied_final_score_is_an_error() -> None:
    document = synthetic_document()
    document.final_score.team_b = document.final_score.team_a

    assert "TIED_FINAL_SCORE" in codes(document)


def test_recognition_review_warnings_name_the_exact_fields() -> None:
    document = synthetic_document()
    document.recognition = RecognitionDocumentState(
        run_id="run",
        problem_paths=[
            "/teams/0/assistant_coach",
            "/score_events/B/cumulative/18/scorer_jersey",
            "/score_events/B/period/4/boundary",
        ],
    )

    messages = [
        issue.message
        for issue in validate_document(document).issues
        if issue.code == "RECOGNITION_REVIEW_REQUIRED"
    ]
    assert messages == [
        "A 队助理教练员姓名未能从图片中可靠确定，需要人工核对。",
        "B 队累计 18 分的得分号码未能从图片中可靠确定。",
        "B 队第 4 节结束累计分未能与书面节比分对应，需要人工核对。",
    ]


def test_typed_recognition_warning_keeps_its_exact_code_and_location() -> None:
    document = synthetic_document()
    document.recognition = RecognitionDocumentState(
        run_id="run",
        issues=[
            RecognitionIssue(
                code="RUNNING_SCORE_MARK_MISSING",
                path="/score_events/A/cumulative/3/has_score_mark",
                message="A 队累计 3 分未识别到黑点或斜杠。",
                observed=False,
                expected=True,
            )
        ],
    )

    warning = next(
        issue
        for issue in validate_document(document).issues
        if issue.code == "RUNNING_SCORE_MARK_MISSING"
    )
    assert warning.severity == "warning"
    assert warning.paths == ["/score_events/A/cumulative/3/has_score_mark"]


def test_first_assistant_coach_fouls_must_fill_from_the_first_cell() -> None:
    document = synthetic_document()
    document.teams[0].assistant_coach_fouls = [FoulEntry(slot=2, code="C")]

    assert "ASSISTANT_COACH_FOUL_SLOT_GAP" in codes(document)


def test_required_header_and_team_fields_are_reported() -> None:
    document = synthetic_document()
    document.header.competition = ""
    document.teams[0].name = ""

    result = codes(document)
    assert "MISSING_COMPETITION" in result
    assert "MISSING_TEAM_NAME" in result


def test_missing_roster_score_events_and_required_staff_are_field_level_issues() -> None:
    document = synthetic_document()
    document.teams[0].players = []
    document.score_events = []
    document.stated_period_scores = []
    document.final_score.team_a = 0
    document.final_score.team_b = 0
    document.final_score.winner_name = ""
    document.final_score.ended_at = ""
    for official in document.officials:
        official.name = ""
        official.signature = "absent"

    result = codes(document)
    assert {
        "MISSING_ROSTER",
        "MISSING_SCORE_EVENTS",
        "MISSING_PERIOD_SCORE",
        "MISSING_END_TIME",
    } <= result
    missing_periods = [
        issue
        for issue in validate_document(document).issues
        if issue.code == "MISSING_PERIOD_SCORE"
    ]
    assert len(missing_periods) == 4

    assert "MISSING_TABLE_PERSONNEL" not in result
    assert "MISSING_REQUIRED_OFFICIAL" not in result
    assert "MISSING_REQUIRED_SIGNATURE" not in result


def test_period_end_without_boundary_requires_review() -> None:
    document = synthetic_document()
    document.score_events[4].boundary = "none"

    report = validate_document(document)
    assert report.status == "needs_review"
    assert "MISSING_PERIOD_BOUNDARY" in {issue.code for issue in report.issues}
