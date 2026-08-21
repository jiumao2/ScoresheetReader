from __future__ import annotations

import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from scoresheet_reader.models import FoulEntry, FoulMarkStyle
from scoresheet_reader.renderer import build_scene, render_pdf, render_svg

from .synthetic_fixture import synthetic_document


def test_svg_and_pdf_share_the_exact_same_semantic_scene(blank_template: Path) -> None:
    document = synthetic_document()
    scene = build_scene(document)
    svg = render_svg(document)

    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert 'viewBox="0 0 595.32 842.04"' in svg
    assert svg.count("data-field-id=") == len(scene)
    assert 'data-field-id="score.A.003.mark"' in svg
    assert 'data-field-id="score.A.006.three_point"' in svg
    assert 'data-field-id="team.A.player.02.foul.1"' in svg
    assert re.search(r'<circle[^>]+data-field-id="score\.A\.003\.mark"[^>]+fill="#11110f"', svg)

    pdf = render_pdf(document, blank_template)
    output = blank_template.parent / "rendered.pdf"
    output.write_bytes(pdf)
    reader = PdfReader(output)
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(595.32, abs=0.001)
    assert float(page.mediabox.height) == pytest.approx(842.04, abs=0.001)
    assert len(reader.pages) == 1
    assert "示例学院甲" in (page.extract_text() or "")


def test_unresolved_score_event_renders_the_jersey_without_a_score_mark() -> None:
    document = synthetic_document()
    event = document.score_events[0]
    event.points = None
    event.mark = None
    event.scorer_circled = False

    scene = build_scene(document)
    ids = {item.get("field_id") for item in scene}

    assert f"score.{event.team.value}.{event.cumulative_score:03d}.scorer" in ids
    assert f"score.{event.team.value}.{event.cumulative_score:03d}.mark" not in ids
    assert f"score.{event.team.value}.{event.cumulative_score:03d}.three_point" not in ids


def test_pdf_embeds_the_chinese_font_on_the_supported_local_runtime(blank_template: Path) -> None:
    output = blank_template.parent / "font.pdf"
    output.write_bytes(render_pdf(synthetic_document(), blank_template))
    page = PdfReader(output).pages[0]
    fonts = page["/Resources"]["/Font"]

    embedded = False
    for font_reference in fonts.values():
        font = font_reference.get_object()
        descriptor = font.get("/FontDescriptor")
        if descriptor and any(
            key in descriptor.get_object() for key in ("/FontFile", "/FontFile2", "/FontFile3")
        ):
            embedded = True
        descendants = font.get("/DescendantFonts", [])
        for descendant in descendants:
            child_descriptor = descendant.get_object().get("/FontDescriptor")
            if child_descriptor and any(
                key in child_descriptor.get_object()
                for key in ("/FontFile", "/FontFile2", "/FontFile3")
            ):
                embedded = True
    assert embedded, "Configure SCORESHEET_FONT_PATH to a TrueType Chinese font."


def test_measured_text_and_box_marks_are_centered_in_both_axes() -> None:
    scene = build_scene(synthetic_document())
    header_a = next(item for item in scene if item.get("field_id") == "header.team_a_name")
    team_a = next(item for item in scene if item.get("field_id") == "team.A.name")
    timeout = next(item for item in scene if item.get("field_id") == "team.A.timeout.H1.1")

    assert header_a["anchor"] == "middle"
    assert header_a["x"] == pytest.approx((94.8 + 278.7) / 2, abs=0.001)
    assert team_a["anchor"] == "middle"
    assert team_a["x"] == pytest.approx((66.28 + 322.6) / 2, abs=0.001)
    assert timeout["x"] == pytest.approx((55.04 + 71.06) / 2, abs=0.001)
    assert timeout["y"] == pytest.approx((154.68 + 165.12) / 2, abs=0.001)
    assert timeout["vertical"] == "middle"

    unused = [item for item in scene if item.get("field_id") == "team.A.timeout.H1.2"]
    assert len(unused) == 2
    assert all(71.06 < item["x1"] < item["x2"] < 87.08 for item in unused)
    assert sum(item["y1"] for item in unused) / 2 == pytest.approx((154.68 + 165.12) / 2, abs=0.001)


def test_coach_uses_three_formal_cells_and_an_unboxed_post_foul_column() -> None:
    scene = build_scene(synthetic_document())
    ids = [item.get("field_id", "") for item in scene]

    assert any(field.startswith("team.A.coach_foul.3") for field in ids)
    assert not any(field.startswith("team.A.coach_foul.4") for field in ids)
    assert any(field.startswith("team.A.coach_post_foul.1") for field in ids)
    assert any(field.startswith("team.A.coach_post_foul.2") for field in ids)

    post_marks = [
        item
        for item in scene
        if item.get("field_id", "").startswith("team.A.coach_post_foul") and item["type"] == "text"
    ]
    assert [item["value"] for item in post_marks] == ["GD", "F"]
    assert all(332.52 < item["x"] < 346.2 for item in post_marks)
    assert all(item["size"] <= 4.5 for item in post_marks)


def test_first_assistant_coach_disqualification_uses_own_three_cells() -> None:
    scene = build_scene(synthetic_document())
    marks = [
        item
        for item in scene
        if item.get("field_id", "").startswith("team.A.assistant_coach_foul")
        and item["type"] == "text"
    ]

    assert [item["value"] for item in marks] == ["D", "2", "F", "F"]
    assistant_center = (380.52 + 392.88) / 2
    assert all(abs(item["y"] - assistant_center) < 5 for item in marks)


def test_unused_coach_and_assistant_foul_cells_share_one_centered_closure_line() -> None:
    document = synthetic_document()
    team = document.teams[0]
    team.coach_fouls = []
    team.assistant_coach_fouls = [FoulEntry(slot=1, code="C")]
    scene = build_scene(document)

    head = next(item for item in scene if item.get("field_id") == "team.A.coach_foul.unused")
    assistant = next(
        item for item in scene if item.get("field_id") == "team.A.assistant_coach_foul.unused"
    )
    head_center = (367.8 + 380.04) / 2
    assistant_center = (380.52 + 392.88) / 2

    assert head == {
        "type": "line",
        "x1": pytest.approx(285.96 + 1.2),
        "y1": pytest.approx(head_center),
        "x2": pytest.approx(332.28 - 1.2),
        "y2": pytest.approx(head_center),
        "width": 0.85,
        "field_id": "team.A.coach_foul.unused",
    }
    assert assistant["x1"] == pytest.approx(301.2 + 1.2)
    assert assistant["x2"] == pytest.approx(332.28 - 1.2)
    assert assistant["y1"] == pytest.approx(assistant_center)
    assert assistant["x2"] < 332.52

    team.coach_fouls = [FoulEntry(slot=slot, code="C") for slot in range(1, 4)]
    scene = build_scene(document)
    assert not any(item.get("field_id") == "team.A.coach_foul.unused" for item in scene)


def test_cancelled_foul_is_written_as_pc_on_one_baseline() -> None:
    document = synthetic_document()
    document.teams[0].players[0].fouls = [FoulEntry(slot=1, code="P", cancelled=True)]
    scene = build_scene(document)
    marks = [item for item in scene if item.get("field_id") == "team.A.player.01.foul.1"]

    assert len(marks) == 1
    assert marks[0]["type"] == "text"
    assert marks[0]["value"] == "Pc"
    assert marks[0]["y"] == pytest.approx((214.2 + 226.8) / 2 + 2.5)


def test_future_circle_style_is_an_independent_rendering_decorator() -> None:
    document = synthetic_document()
    document.teams[0].players[0].fouls = [
        FoulEntry(
            slot=1,
            code="T",
            catalog_id="player.technical_category_1",
            mark_style=FoulMarkStyle.CIRCLED,
        )
    ]
    scene = build_scene(document)

    circle_mark = next(
        item for item in scene if item.get("field_id") == "team.A.player.01.foul.1.circle"
    )
    assert circle_mark["type"] == "circle"


def test_game_end_marks_are_derived_from_matching_final_scores() -> None:
    document = synthetic_document()
    for event in document.score_events:
        if event.boundary == "game_end":
            event.boundary = "none"
    scene = build_scene(document)

    for field in ("score.A.021.boundary", "score.B.018.boundary"):
        primitives = [item for item in scene if item.get("field_id") == field]
        assert [item["type"] for item in primitives].count("circle") == 1
        assert [item["type"] for item in primitives].count("line") == 2


def test_roster_closure_matches_fiba_horizontal_then_diagonal_convention() -> None:
    eleven = synthetic_document()
    eleven.teams[0].players = eleven.teams[0].players[:11]
    eleven_scene = build_scene(eleven)
    eleven_marks = [
        item
        for item in eleven_scene
        if item.get("field_id", "").startswith("team.A.roster_closure")
    ]
    assert len(eleven_marks) == 1
    assert eleven_marks[0] == {
        "type": "line",
        "x1": 37.2,
        "y1": pytest.approx((354.6 + 367.2) / 2),
        "x2": 255.0,
        "y2": pytest.approx((354.6 + 367.2) / 2),
        "width": 1.1,
        "field_id": "team.A.roster_closure.horizontal",
    }

    nine = synthetic_document()
    nine.teams[0].players = nine.teams[0].players[:9]
    nine_scene = build_scene(nine)
    horizontal = next(
        item for item in nine_scene if item.get("field_id") == "team.A.roster_closure.horizontal"
    )
    diagonal = next(
        item for item in nine_scene if item.get("field_id") == "team.A.roster_closure.diagonal"
    )
    assert horizontal["x2"] == 255.0
    assert horizontal["y1"] == pytest.approx((328.8 + 342.0) / 2)
    assert diagonal["x1"] == 255.0
    assert diagonal["y1"] == horizontal["y1"]
    assert diagonal["x2"] == 331.8
    assert diagonal["y2"] == 367.2
