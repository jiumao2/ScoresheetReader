from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import ChangeLogAction, FieldChange, ScoresheetDocument

SOURCE_ACTIONS: dict[str, ChangeLogAction] = {
    "human": "human_edit",
    "undo": "undo",
    "redo": "redo",
    "recognition_merge": "recognition_merge",
    "reupload": "reupload",
    "confirm": "confirm",
}

ACTION_SUMMARIES: dict[ChangeLogAction, str] = {
    "human_edit": "人工编辑",
    "undo": "撤销修改",
    "redo": "重做修改",
    "recognition_merge": "应用识别差异",
    "reupload": "重新上传记录表并重置草稿",
    "confirm": "提交记录表",
}


def _keyed(items: list[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        identity = "-".join(str(item.get(key, "")) for key in keys) or str(index + 1)
        result[identity] = item
    return result


def _normalize_fouls(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _keyed(entries, "slot")


def _normalize_player(player: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(player)
    normalized["fouls"] = _normalize_fouls(list(player.get("fouls") or []))
    normalized["post_foul_markers"] = _normalize_fouls(list(player.get("post_foul_markers") or []))
    return normalized


def _normalize_team(team: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(team)
    normalized.pop("side", None)
    normalized["players"] = {
        str(player.get("row", index + 1)): _normalize_player(player)
        for index, player in enumerate(team.get("players") or [])
    }
    normalized["timeouts"] = _keyed(list(team.get("timeouts") or []), "scope", "slot")
    normalized["team_fouls"] = _keyed(list(team.get("team_fouls") or []), "period")
    normalized["coach_fouls"] = _normalize_fouls(list(team.get("coach_fouls") or []))
    normalized["coach_post_foul_markers"] = _normalize_fouls(
        list(team.get("coach_post_foul_markers") or [])
    )
    normalized["assistant_coach_fouls"] = _normalize_fouls(
        list(team.get("assistant_coach_fouls") or [])
    )
    normalized["assistant_coach_post_foul_markers"] = _normalize_fouls(
        list(team.get("assistant_coach_post_foul_markers") or [])
    )
    return normalized


def semantic_document(document: ScoresheetDocument) -> dict[str, Any]:
    """Return only editable scoresheet content with stable domain keys."""
    payload = document.model_dump(mode="json")
    recognition = payload.get("recognition") or {}
    return {
        "header": payload["header"],
        "teams": {team["side"]: _normalize_team(team) for team in payload.get("teams") or []},
        "score_events": _keyed(list(payload.get("score_events") or []), "sequence"),
        "stated_period_scores": _keyed(list(payload.get("stated_period_scores") or []), "period"),
        "final_score": payload["final_score"],
        "officials": _keyed(list(payload.get("officials") or []), "role"),
        "table_personnel": list(recognition.get("table_personnel") or []),
    }


def _escape_path(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _diff(before: Any, after: Any, path: str, output: list[FieldChange]) -> None:
    if before == after:
        return
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after), key=str):
            _diff(
                before.get(key),
                after.get(key),
                f"{path}/{_escape_path(str(key))}",
                output,
            )
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            _diff(
                before[index] if index < len(before) else None,
                after[index] if index < len(after) else None,
                f"{path}/{index}",
                output,
            )
        return
    output.append(FieldChange(path=path or "/", before=before, after=after))


def document_changes(
    before: ScoresheetDocument,
    after: ScoresheetDocument,
) -> list[FieldChange]:
    changes: list[FieldChange] = []
    _diff(semantic_document(before), semantic_document(after), "", changes)
    return changes


def log_payload_for_update(
    before: ScoresheetDocument,
    after: ScoresheetDocument,
    source: str,
) -> tuple[ChangeLogAction, str, list[FieldChange]] | None:
    action = SOURCE_ACTIONS.get(source)
    if action is None:
        return None
    if action in {"reupload", "confirm"}:
        return action, ACTION_SUMMARIES[action], []
    changes = document_changes(before, after)
    if not changes:
        return None
    summary = f"{ACTION_SUMMARIES[action]} · {len(changes)} 项"
    return action, summary, changes
