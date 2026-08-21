from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from openpyxl import Workbook

from scoresheet_reader.master_data import MasterDataValidationError, load_master_data
from scoresheet_reader.settings import REPOSITORY_ROOT, Settings


def _write_roster(path: Path, duplicate: bool = False) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "男甲"
    sheet.append(["队伍", "姓名", "球衣号码"])
    sheet.append(["数学", "  张  三  ", 99])
    sheet.append(["数学", "张 三" if duplicate else "李四", 7])
    workbook.save(path)


def _write_empty_roster(path: Path) -> None:
    workbook = Workbook()
    workbook.active.title = "女甲"
    workbook.active.append(["队伍", "姓名", "球衣号码"])
    workbook.save(path)


def _write_schedule(path: Path) -> None:
    row = {
        "_id": "game-1",
        "group": "男甲",
        "home_team": "数学",
        "away_team": "数学",
        "place": "第一体育馆",
        "time": {"$date": "2026-03-21T06:20:00Z"},
        "home_team_score": 99,
        "away_team_score": 88,
        "CC": "不应进入先验",
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_jsonl_and_xlsx_import_normalizes_unique_names_and_ignores_jerseys(
    tmp_path: Path,
) -> None:
    _write_schedule(tmp_path / "Schedule_demo.json")
    _write_roster(tmp_path / "男篮.xlsx")
    _write_empty_roster(tmp_path / "女篮.xlsx")
    settings = Settings(
        repository_root=REPOSITORY_ROOT,
        data_dir=tmp_path / "data",
        master_data_dir=tmp_path,
        competition_name="测试杯",
    )

    bundle = load_master_data(settings)

    assert bundle is not None
    team = bundle.teams[0]
    assert [name for _, name in team.players] == ["张 三", "李四"]
    assert all(len(player) == 2 for player in team.players)
    assert bundle.games[0].scheduled_time == "14:20"
    assert bundle.games[0].competition == "测试杯"


def test_duplicate_normalized_name_blocks_roster_import(tmp_path: Path) -> None:
    _write_schedule(tmp_path / "Schedule_demo.json")
    _write_roster(tmp_path / "男篮.xlsx", duplicate=True)
    _write_empty_roster(tmp_path / "女篮.xlsx")
    settings = Settings(
        repository_root=REPOSITORY_ROOT,
        data_dir=tmp_path / "data",
        master_data_dir=tmp_path,
    )

    with pytest.raises(MasterDataValidationError, match="重复唯一姓名"):
        load_master_data(settings)


@pytest.mark.skipif(
    os.getenv("RUN_PRIVATE_MASTER_TEST") != "1",
    reason="private master-data audit requires RUN_PRIVATE_MASTER_TEST=1",
)
def test_private_master_data_resolves_142_of_146_games() -> None:
    if not (REPOSITORY_ROOT / "test" / "Schedule_2026北大杯.json").exists():
        pytest.skip("private local master data is unavailable")
    bundle = load_master_data(
        Settings(
            repository_root=REPOSITORY_ROOT,
            data_dir=REPOSITORY_ROOT / "tmp" / "private-master-test",
            master_data_dir=REPOSITORY_ROOT / "test",
        )
    )

    assert bundle is not None
    assert len(bundle.games) == 146
    assert sum(game.ready for game in bundle.games) == 142
    assert sum(not game.ready for game in bundle.games) == 4
    assert sum(len(team.players) for team in bundle.teams) == 840
