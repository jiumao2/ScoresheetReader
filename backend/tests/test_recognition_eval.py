from scoresheet_reader.recognition_eval import evaluate_recognition


def test_recognition_evaluation_distinguishes_detection_period_and_scorer_errors() -> None:
    truth = {
        "team_a_running_score": [
            {"period": 1, "cumulative_score": 2, "points": 2, "scorer_jersey": "4"},
            {"period": 1, "cumulative_score": 3, "points": 1, "scorer_jersey": "5"},
        ],
        "team_b_running_score": [
            {"period": 1, "cumulative_score": 2, "points": 2, "scorer_jersey": "6"},
        ],
        "period_scores": [{"period": 1, "team_a": 3, "team_b": 2}],
        "final_score": {"team_a": 3, "team_b": 2},
        "team_b_names": {"4": "王五"},
    }
    result = {
        "team_a": {},
        "team_b": {
            "players": [{"row": 4, "name": None}],
        },
        "running_score_rows": [
            {
                "cumulative_score": 1,
                "team_a": {"scorer_jersey": None, "points": None, "has_score_mark": False},
                "team_b": {"scorer_jersey": None, "points": None, "has_score_mark": False},
            },
            {
                "cumulative_score": 2,
                "team_a": {"scorer_jersey": "9", "points": 2, "has_score_mark": True},
                "team_b": {"scorer_jersey": "6", "points": 2, "has_score_mark": True},
            },
            {
                "cumulative_score": 3,
                "team_a": {"scorer_jersey": None, "points": None, "has_score_mark": False},
                "team_b": {"scorer_jersey": None, "points": None, "has_score_mark": False},
            },
            {
                "cumulative_score": 4,
                "team_a": {"scorer_jersey": "5", "points": 2, "has_score_mark": True},
                "team_b": {"scorer_jersey": None, "points": None, "has_score_mark": False},
            },
        ],
        "period_scores": [{"period": 1, "team_a": 3, "team_b": 2}],
        "final_score": {"team_a": 3, "team_b": 2},
    }

    report = evaluate_recognition(result, truth)

    assert report["running_score"]["team_a"]["missing_cumulative_scores"] == [3]
    assert report["running_score"]["team_a"]["extra_cumulative_scores"] == [4]
    assert report["running_score"]["combined"]["matched_cumulative_scores"] == 2
    assert report["running_score"]["combined"]["exact_events"] == 1
    assert report["running_score"]["team_b"]["points_accuracy_on_matched"] == 1.0
    assert report["period_scores_exact"] is True
    assert report["final_score_exact"] is True
    assert report["target_player_names_exact"] is False
