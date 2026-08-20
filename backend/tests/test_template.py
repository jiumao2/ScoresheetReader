from __future__ import annotations

from scoresheet_reader.template import load_template_definition


def test_template_has_measured_a4_geometry() -> None:
    definition = load_template_definition()

    assert definition["page"] == {"width": 595.32, "height": 842.04}
    assert definition["coordinate_system"] == "pdf_points_top_left"
    assert definition["player_columns"]["fouls"] == [
        [255.0, 270.6],
        [270.6, 285.6],
        [285.6, 301.2],
        [301.2, 316.2],
        [316.2, 331.8],
    ]
    assert definition["team_layouts"]["A"]["player_rows"][0] == 214.2
    assert definition["team_layouts"]["B"]["player_rows"][-1] == 634.8
    assert definition["team_layouts"]["B"]["timeouts"]["H1"]["cells"][0] == [
        55.04,
        422.68,
        70.34,
        433.12,
    ]
    assert definition["player_columns"]["coach_fouls"] == [
        [285.96, 301.2],
        [301.2, 316.68],
        [316.68, 332.28],
    ]
    assert definition["player_columns"]["post_foul"] == [332.52, 346.2]
    assert definition["running_score"]["group_boundaries"] == [
        346.2,
        402.6,
        459.0,
        516.0,
        572.4,
    ]


def test_every_editable_cell_has_a_unique_stable_id_and_pdf_rect() -> None:
    definition = load_template_definition()
    cells = definition["cells"]
    ids = [cell["id"] for cell in cells]

    assert len(cells) == 662
    assert len(ids) == len(set(ids))
    assert "team.A.player.01.foul.1" in ids
    assert "team.A.player.01.post_foul" in ids
    assert "team.B.coach_foul.3" in ids
    assert "team.B.coach_post_foul" in ids
    assert "team.B.assistant_coach_foul.3" in ids
    assert "team.B.assistant_coach_post_foul" in ids
    assert "team.B.coach_foul.4" not in ids
    assert "score.B.160" in ids
    assert "summary.final.A" in ids
    for cell in cells:
        rect = cell["rect"]
        assert rect["width"] > 0
        assert rect["height"] > 0
        assert -0.5 <= rect["x"] <= definition["page"]["width"] + 0.5
        assert -0.5 <= rect["y"] <= definition["page"]["height"] + 0.5
        assert cell["editor"]
        assert cell["data_path"]
