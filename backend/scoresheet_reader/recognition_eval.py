from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _with_derived_points(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Add points to legacy private truth rows that predate the v14 output field."""
    previous = 0
    derived: list[dict[str, Any]] = []
    for raw in sorted(events, key=lambda item: int(item["cumulative_score"])):
        item = dict(raw)
        cumulative = int(item["cumulative_score"])
        item.setdefault("points", cumulative - previous)
        previous = cumulative
        derived.append(item)
    return derived


def _score_events(
    predicted: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    predicted_by_score = {int(item["cumulative_score"]): item for item in predicted}
    expected_by_score = {int(item["cumulative_score"]): item for item in expected}
    matched_scores = sorted(predicted_by_score.keys() & expected_by_score.keys())
    exact_period = sum(
        predicted_by_score[score].get("period") == expected_by_score[score].get("period")
        for score in matched_scores
    )
    exact_scorer = sum(
        predicted_by_score[score].get("scorer_jersey")
        == expected_by_score[score].get("scorer_jersey")
        for score in matched_scores
    )
    exact_points = sum(
        predicted_by_score[score].get("points") == expected_by_score[score].get("points")
        for score in matched_scores
    )
    exact_events = sum(
        predicted_by_score[score].get("period") == expected_by_score[score].get("period")
        and predicted_by_score[score].get("scorer_jersey")
        == expected_by_score[score].get("scorer_jersey")
        and predicted_by_score[score].get("points") == expected_by_score[score].get("points")
        for score in matched_scores
    )
    precision = _ratio(len(matched_scores), len(predicted_by_score))
    recall = _ratio(len(matched_scores), len(expected_by_score))
    exact_precision = _ratio(exact_events, len(predicted_by_score))
    exact_recall = _ratio(exact_events, len(expected_by_score))
    return {
        "predicted": len(predicted_by_score),
        "expected": len(expected_by_score),
        "matched_cumulative_scores": len(matched_scores),
        "missing_cumulative_scores": sorted(expected_by_score.keys() - predicted_by_score.keys()),
        "extra_cumulative_scores": sorted(predicted_by_score.keys() - expected_by_score.keys()),
        "cumulative_precision": precision,
        "cumulative_recall": recall,
        "cumulative_f1": _f1(precision, recall),
        "period_accuracy_on_matched": _ratio(exact_period, len(matched_scores)),
        "scorer_accuracy_on_matched": _ratio(exact_scorer, len(matched_scores)),
        "points_accuracy_on_matched": _ratio(exact_points, len(matched_scores)),
        "exact_events": exact_events,
        "exact_event_precision": exact_precision,
        "exact_event_recall": exact_recall,
        "exact_event_f1": _f1(exact_precision, exact_recall),
    }


def _combine_event_metrics(
    predicted: Mapping[str, Sequence[Mapping[str, Any]]],
    expected: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    predicted_items = [
        {**item, "side": side} for side, entries in predicted.items() for item in entries
    ]
    expected_items = [
        {**item, "side": side} for side, entries in expected.items() for item in entries
    ]
    predicted_by_key = {
        (item["side"], int(item["cumulative_score"])): item for item in predicted_items
    }
    expected_by_key = {
        (item["side"], int(item["cumulative_score"])): item for item in expected_items
    }
    matched = sorted(predicted_by_key.keys() & expected_by_key.keys())
    exact_events = sum(
        predicted_by_key[key].get("period") == expected_by_key[key].get("period")
        and predicted_by_key[key].get("scorer_jersey") == expected_by_key[key].get("scorer_jersey")
        and predicted_by_key[key].get("points") == expected_by_key[key].get("points")
        for key in matched
    )
    precision = _ratio(len(matched), len(predicted_by_key))
    recall = _ratio(len(matched), len(expected_by_key))
    exact_precision = _ratio(exact_events, len(predicted_by_key))
    exact_recall = _ratio(exact_events, len(expected_by_key))
    return {
        "predicted": len(predicted_by_key),
        "expected": len(expected_by_key),
        "matched_cumulative_scores": len(matched),
        "cumulative_precision": precision,
        "cumulative_recall": recall,
        "cumulative_f1": _f1(precision, recall),
        "exact_events": exact_events,
        "exact_event_precision": exact_precision,
        "exact_event_recall": exact_recall,
        "exact_event_f1": _f1(exact_precision, exact_recall),
    }


def evaluate_recognition(
    result: Mapping[str, Any],
    truth: Mapping[str, Any],
) -> dict[str, Any]:
    """Score a compact recognition payload against a manually audited private truth."""
    period_scores = sorted(result.get("period_scores", []), key=lambda item: item.get("period", 0))

    def with_derived_periods(side: str) -> list[dict[str, Any]]:
        score_field = "team_a" if side == "A" else "team_b"
        checkpoints: list[tuple[int, int]] = []
        total = 0
        for period_score in period_scores:
            value = period_score.get(score_field)
            if value is None:
                continue
            total += int(value)
            checkpoints.append((int(period_score["period"]), total))
        rows = result.get("running_score_rows")
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            side_key = f"team_{side.lower()}"
            sparse = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                evidence = row.get(side_key)
                if not isinstance(evidence, Mapping) or evidence.get("scorer_jersey") is None:
                    continue
                sparse.append(
                    {
                        "cumulative_score": row.get("cumulative_score"),
                        "points": evidence.get("points"),
                        "scorer_jersey": evidence.get("scorer_jersey"),
                    }
                )
        else:
            sparse = result.get(f"team_{side.lower()}", {}).get("running_score", [])
        derived: list[dict[str, Any]] = []
        for raw in sparse:
            item = dict(raw)
            if item.get("period") is None:
                item["period"] = next(
                    (
                        period
                        for period, checkpoint in checkpoints
                        if item["cumulative_score"] <= checkpoint
                    ),
                    checkpoints[-1][0] if checkpoints else 1,
                )
            derived.append(item)
        return derived

    predicted_events = {"A": with_derived_periods("A"), "B": with_derived_periods("B")}
    expected_events = {
        "A": _with_derived_points(truth.get("team_a_running_score", [])),
        "B": _with_derived_points(truth.get("team_b_running_score", [])),
    }
    player_name_checks: list[dict[str, Any]] = []
    expected_names = truth.get("team_b_names", {})
    players_by_row = {
        str(player.get("row")): player.get("name")
        for player in result.get("team_b", {}).get("players", [])
    }
    for row, expected_name in expected_names.items():
        observed = players_by_row.get(str(row))
        player_name_checks.append(
            {
                "side": "B",
                "row": int(row),
                "expected": expected_name,
                "observed": observed,
                "exact": observed == expected_name,
            }
        )
    expected_period_scores = truth.get("period_scores", [])
    observed_period_scores = result.get("period_scores", [])
    expected_final = truth.get("final_score", {})
    observed_final = result.get("final_score", {})
    return {
        "running_score": {
            "team_a": _score_events(predicted_events["A"], expected_events["A"]),
            "team_b": _score_events(predicted_events["B"], expected_events["B"]),
            "combined": _combine_event_metrics(predicted_events, expected_events),
        },
        "period_scores_exact": observed_period_scores == expected_period_scores,
        "final_score_exact": all(
            observed_final.get(key) == expected_final.get(key) for key in ("team_a", "team_b")
        ),
        "target_player_names": player_name_checks,
        "target_player_names_exact": all(item["exact"] for item in player_name_checks),
    }
