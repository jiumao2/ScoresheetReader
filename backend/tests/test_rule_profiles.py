from __future__ import annotations

import json
from pathlib import Path


def test_rule_profiles_preserve_current_defaults_and_reserve_2026_notation() -> None:
    path = Path(__file__).parents[2] / "shared" / "rule_profiles.json"
    profiles = json.loads(path.read_text(encoding="utf-8"))

    assert profiles["fiba_2024"]["enabled_in_editor"] is True
    assert profiles["fiba_2026"]["enabled_in_editor"] is False
    assert profiles["fiba_2026"]["effective_from"] == "2026-10-01"

    active_markings = profiles["fiba_2024"]["foul_markings"]
    for marking in active_markings:
        assert marking["editor_groups"]
        assert "" in marking["allowed_suffixes"]

    def editor_codes(group: str) -> list[str]:
        return list(
            dict.fromkeys(
                marking["code"]
                for marking in active_markings
                if group in marking["editor_groups"]
            )
        )

    assert editor_codes("player") == ["P", "T", "U", "D"]
    assert editor_codes("coach") == ["C", "B", "D", "F"]
    assert editor_codes("post_foul") == ["D", "GD", "F"]
    coach_d = next(marking for marking in active_markings if marking["id"] == "coach.disqualifying")
    assert coach_d["allowed_suffixes"] == ["", "1", "2", "3", "c"]

    future = {entry["id"]: entry for entry in profiles["fiba_2026"]["foul_markings"]}
    assert future["player.technical_category_1"]["code"] == "T"
    assert future["player.technical_category_1"]["style"] == "circled"
    assert future["player.technical_category_1"]["subjects"] == ["player"]
    assert future["player.technical_category_2"]["code"] == "T"
    assert future["player.technical_category_2"]["style"] == "plain"
    assert future["player.disruptive"]["code"] == "DI"
    assert future["player.flagrant"]["code"] == "FL"
    assert future["system.delegation_disqualification"]["code"] == "BD"
