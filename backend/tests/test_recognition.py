from __future__ import annotations

import hashlib
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from scoresheet_reader.models import GamePriorSnapshot, PriorTeam, RuleProfileId, TeamSide
from scoresheet_reader.recognition import (
    PROMPT_VERSION,
    QWEN_DATA_URI_MAX_BYTES,
    SYSTEM_PROMPT,
    QwenRecognitionProvider,
    RecognitionContext,
    RecognitionProviderError,
    _data_url_size,
    _encode_jpeg,
    _prepare_image,
    _score_events,
    build_payload_model,
    build_user_prompt,
    normalize_provider_payload,
    normalize_running_score_payload,
    sanitize_unknown_player_names,
)
from scoresheet_reader.settings import REPOSITORY_ROOT, Settings


def _prior() -> GamePriorSnapshot:
    return GamePriorSnapshot(
        game_id="game",
        competition="测试杯",
        division="测试组",
        date="2026-08-19",
        scheduled_time="14:00",
        venue="测试球馆",
        team_a=PriorTeam(team_id="secret-a", name="甲队", player_names=["张三", "李四"]),
        team_b=PriorTeam(team_id="secret-b", name="乙队", player_names=["王五", "赵六"]),
        source_hash="private-source-hash",
    )


def _valid_payload() -> dict:
    empty_team = {
        "players": [],
        "timeouts": [],
        "team_fouls": [],
        "head_coach": {"name": None, "fouls": []},
        "assistant_coach": {"name": None, "fouls": []},
        "running_score": [],
    }
    return {
        "period_scores": [{"period": period, "team_a": 0, "team_b": 0} for period in range(1, 5)],
        "final_score": {
            "team_a": 0,
            "team_b": 0,
            "winner_name": None,
            "ended_at": None,
        },
        "team_a": {**empty_team},
        "team_b": {**empty_team},
        "table_personnel": [],
        "officials": [],
        "recognition_notes": "",
    }


def test_concurrent_identical_recognition_requests_share_one_active_run(
    recognition_client,
    sample_png: bytes,
) -> None:
    service = recognition_client.app.state.recognition_service
    original_provider = service.provider
    started = Event()
    release = Event()

    class BlockingProvider:
        def recognize(self, **kwargs):
            started.set()
            assert release.wait(timeout=2)
            return original_provider.recognize(**kwargs)

    service.provider = BlockingProvider()
    game = next(item for item in recognition_client.get("/api/v1/games").json() if item["ready"])
    uploaded = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    ).json()
    document = uploaded["document"]
    assert started.wait(timeout=2)
    assert recognition_client.get("/api/v1/games").json()[0]["scoresheet_state"] == ("recognizing")
    barrier = Barrier(2)

    def create_run():
        barrier.wait()
        return service.create_run(document["id"], document["revision"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create_run(), range(2)))

    release.set()
    assert results[0][0].id == uploaded["recognition_run"]["id"]
    assert results[1][0].id == uploaded["recognition_run"]["id"]
    assert [needs_execution for _, needs_execution in results] == [False, False]


def test_dynamic_schema_is_small_and_only_accepts_each_teams_unique_names() -> None:
    prior = _prior()
    model = build_payload_model(
        prior,
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    schema = model.model_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False)

    assert "张三" in schema_text
    assert "王五" in schema_text
    assert "secret-a" not in schema_text
    assert "private-source-hash" not in schema_text
    assert "certainty" not in schema_text
    assert "alternative" not in schema_text
    assert "P2c" not in schema_text
    score_schema = schema["$defs"]["RecognizedScoreEvent"]["properties"]
    assert score_schema["cumulative_score"]["minimum"] == 1
    assert score_schema["cumulative_score"]["maximum"] == 160
    assert set(score_schema) == {"cumulative_score", "scorer_jersey", "points"}
    assert score_schema["points"]["enum"] == [1, 2, 3]
    assert "running_score_rows" not in schema["properties"]
    assert "running_score" in schema["$defs"]["RecognizedTeamA"]["properties"]
    coach_marks = schema["$defs"]["RecognizedCoachFoul"]["properties"]["mark"]["enum"]
    assert {"D1", "D2", "D3", "Dc"} <= set(coach_marks)

    descriptions: list[str] = []

    def collect_descriptions(value: object) -> None:
        if isinstance(value, dict):
            description = value.get("description")
            if isinstance(description, str):
                descriptions.append(description)
            for child in value.values():
                collect_descriptions(child)
        elif isinstance(value, list):
            for child in value:
                collect_descriptions(child)

    collect_descriptions(schema)
    assert descriptions
    assert all(re.search(r"[\u3400-\u9fff]", text) for text in descriptions)

    invalid = {
        "team_a": {
            "players": [
                {
                    "row": 1,
                    "name": "不在名单",
                    "jersey_number": "7",
                    "captain": False,
                    "participation": "starter",
                    "fouls": [],
                }
            ],
            "timeouts": [],
            "team_fouls": [],
            "head_coach": {"name": None, "fouls": []},
            "assistant_coach": {"name": None, "fouls": []},
            "running_score": [],
        },
        "team_b": {
            "players": [],
            "timeouts": [],
            "team_fouls": [],
            "head_coach": {"name": None, "fouls": []},
            "assistant_coach": {"name": None, "fouls": []},
            "running_score": [],
        },
        "period_scores": [{"period": period, "team_a": 0, "team_b": 0} for period in range(1, 5)],
        "final_score": {"team_a": None, "team_b": None, "winner_name": None, "ended_at": None},
        "table_personnel": [],
        "officials": [],
        "recognition_notes": "A队第1行姓名无法确定",
    }
    with pytest.raises(ValidationError):
        model.model_validate(invalid)


def test_prompt_explains_only_ambiguous_visual_semantics() -> None:
    user_prompt = build_user_prompt(_prior(), RuleProfileId.FIBA_2024)
    prompt_text = " ".join(SYSTEM_PROMPT.split())

    assert PROMPT_VERSION == "scoresheet-2026-08-20-v24-cn"
    assert "【任务与输出边界】" in prompt_text
    assert "记录表可以分为以下几个区域" in prompt_text
    assert "总体浏览之后，你需要依次逐块放大查看仔细阅读" in prompt_text
    assert "暂停格有三行" in prompt_text
    assert "【记录台与裁判签名区-记录台区域读取】" in prompt_text
    assert "【交叉验证——输出前必须逐项执行】" in prompt_text
    assert "一、登记号码与得分号码验证" in prompt_text
    assert "二、得分事件数量验证" in prompt_text
    assert "三、累计分差值与得分符号验证" in prompt_text
    assert "四、最终比分与胜队验证" in prompt_text
    assert "五、冲突处理" in prompt_text
    assert "全队犯规区每一节会有4个格子" in prompt_text
    assert "按照从左到右的顺序使用" in prompt_text
    assert "双横线“=”表示该格未使用" in prompt_text
    assert "统计每一节比赛格子的使用数量" in prompt_text
    assert "左上、右上、左下、右下依次为第1,2,3,4节" in prompt_text
    assert "【累积分区-逐次得分读取】" in prompt_text
    assert "切勿将累计分识别为队员号码" in prompt_text
    assert "应该首先识别外侧号码作为得分依据" in prompt_text
    assert "登记的外侧号码之间的上下间隔为该次得分分值" in prompt_text
    assert "形状固定为N×6" in prompt_text
    assert "team，填写A或B" in prompt_text
    assert "cumulative_score，填写该次得分对应的印刷累积分" in prompt_text
    assert "scorer_jersey，填写160行表格中该侧的外侧手写号码或null" in prompt_text
    assert "points，1、2、3" in prompt_text
    assert "score_mark_matches_points" in prompt_text
    assert "scorer_jersey_matches_records" in prompt_text
    assert "检查表和复核过程无需输出" in prompt_text
    assert "Pc中的c与P位于同一基线，不存在P2c" in prompt_text
    assert "{foul_notation}" not in prompt_text
    assert "五、节次和书面节比分验证" not in prompt_text
    assert "七、犯规区域的验证边界" not in prompt_text
    assert "table_personnel" in prompt_text
    assert "不分配记录员、助理记录员、计时员或24秒计时员岗位" in prompt_text
    assert "每种role最多输出一次" in prompt_text
    assert "不要输出推理过程" in prompt_text
    assert "张三" not in user_prompt and "王五" not in user_prompt
    assert "每队唯一候选球员姓名只编码在JSON Schema中该队name字段的enum内" in user_prompt
    assert "测试杯" in user_prompt
    assert "甲队" in user_prompt and "乙队" in user_prompt
    assert user_prompt.startswith("请转录整张FIBA 2024篮球记录表")
    assert "Transcribe the entire" not in user_prompt


def test_schema_requires_four_regulation_periods_and_optional_aggregate_ot() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    payload = _valid_payload()
    model.model_validate(payload)

    payload["period_scores"].append({"period": 5, "team_a": 2, "team_b": 1})
    model.model_validate(payload)

    payload["period_scores"][-1]["period"] = 6
    with pytest.raises(ValidationError):
        model.model_validate(payload)

    payload = _valid_payload()
    payload["period_scores"][2]["period"] = 4
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_running_score_requires_points_limited_to_one_two_or_three() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    for points in (1, 2, 3):
        payload = _valid_payload()
        payload["team_a"]["running_score"] = [
            {
                "cumulative_score": points,
                "points": points,
                "scorer_jersey": "4",
            }
        ]
        model.model_validate(payload)

    for invalid_points in (0, 4):
        payload = _valid_payload()
        payload["team_a"]["running_score"] = [
            {
                "cumulative_score": 1,
                "points": invalid_points,
                "scorer_jersey": "4",
            }
        ]
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    payload = _valid_payload()
    payload["team_a"]["running_score"] = [
        {
            "cumulative_score": 1,
            "scorer_jersey": "4",
        }
    ]
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_score_import_retains_model_points_and_reports_delta_conflicts() -> None:
    raw_events = [
        SimpleNamespace(cumulative_score=2, scorer_jersey="4", points=1),
        SimpleNamespace(cumulative_score=6, scorer_jersey="5", points=3),
    ]
    period_scores = [SimpleNamespace(period=1, team_a=6, team_b=0)]
    final_score = SimpleNamespace(team_a=6, team_b=0)
    problems: list[str] = []
    recognition_issues = []

    events = _score_events(
        raw_events,
        TeamSide.A,
        period_scores,
        final_score,
        problems,
        recognition_issues,
    )

    assert [(event.cumulative_score, event.points) for event in events] == [(2, 1), (6, 3)]
    assert any(issue.code == "RUNNING_SCORE_POINTS_MISMATCH" for issue in recognition_issues)
    assert "/score_events/A/cumulative/2/delta" in problems
    assert "/score_events/A/cumulative/6/delta" in problems


def test_score_import_keeps_an_event_with_an_unreadable_jersey_for_review() -> None:
    raw_events = [SimpleNamespace(cumulative_score=2, scorer_jersey=None, points=2)]
    problems: list[str] = []
    recognition_issues = []

    events = _score_events(
        raw_events,
        TeamSide.A,
        [SimpleNamespace(period=1, team_a=2, team_b=0)],
        SimpleNamespace(team_a=2, team_b=0),
        problems,
        recognition_issues,
    )

    assert [(event.cumulative_score, event.points, event.mark) for event in events] == [
        (2, 2, "diagonal")
    ]
    assert events[0].scorer_jersey == ""
    assert "/score_events/A/cumulative/2/scorer_jersey" in problems
    assert recognition_issues == []


def test_schema_only_accepts_a_standard_team_name_as_winner() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    payload = _valid_payload()
    payload["final_score"]["winner_name"] = "甲队"
    model.model_validate(payload)

    payload["final_score"]["winner_name"] = "甲"
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_small_whole_image_is_upscaled_toward_target_with_two_times_axis_cap(tmp_path) -> None:
    source = tmp_path / "small.png"
    Image.new("RGB", (100, 200), "white").save(source)

    payload, _ = _prepare_image(source, 80_000)

    with Image.open(io.BytesIO(payload)) as prepared:
        assert prepared.size == (200, 400)
        assert prepared.width * prepared.height == 80_000


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png")],
)
def test_large_jpeg_and_png_are_sent_without_resampling_or_reencoding(
    tmp_path,
    image_format: str,
    mime_type: str,
) -> None:
    source = tmp_path / f"large.{image_format.lower()}"
    Image.new("RGB", (40, 30), "white").save(source, format=image_format)
    original = source.read_bytes()

    payload, data_url = _prepare_image(source, 1_000)

    assert payload == original
    assert data_url.startswith(f"data:{mime_type};base64,")
    assert len(data_url.encode("ascii")) == _data_url_size(len(payload), mime_type)


def test_large_webp_is_converted_to_same_size_jpeg(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("scoresheet_reader.recognition.QWEN_WEBP_MAX_LONG_EDGE", 10)
    monkeypatch.setattr("scoresheet_reader.recognition.QWEN_WEBP_MAX_SHORT_EDGE", 5)
    source = tmp_path / "large.webp"
    Image.new("RGB", (20, 10), "white").save(source, format="WEBP")

    payload, data_url = _prepare_image(source, 1)

    assert data_url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(payload)) as prepared:
        assert prepared.format == "JPEG"
        assert prepared.size == (20, 10)


def test_oversize_data_url_uses_highest_fitting_jpeg_quality_without_resizing(
    tmp_path,
) -> None:
    source = tmp_path / "noise.png"
    image = Image.effect_noise((96, 96), 100).convert("RGB")
    image.save(source, format="PNG")
    encoded_by_quality = {quality: _encode_jpeg(image, quality) for quality in range(1, 96)}
    limit = _data_url_size(len(encoded_by_quality[50]), "image/jpeg")
    expected_quality = max(
        quality
        for quality, payload in encoded_by_quality.items()
        if _data_url_size(len(payload), "image/jpeg") <= limit
    )
    assert _data_url_size(source.stat().st_size, "image/png") > limit

    payload, data_url = _prepare_image(source, 1, max_data_uri_bytes=limit)

    assert payload == encoded_by_quality[expected_quality]
    assert len(data_url.encode("ascii")) <= limit
    with Image.open(io.BytesIO(payload)) as prepared:
        assert prepared.size == image.size


def test_image_that_cannot_fit_at_quality_one_fails_before_provider_call(tmp_path) -> None:
    source = tmp_path / "sheet.png"
    Image.new("RGB", (20, 20), "white").save(source)

    with pytest.raises(RecognitionProviderError, match="不缩小分辨率"):
        _prepare_image(source, 1, max_data_uri_bytes=10)


def test_default_upscale_target_is_decimal_eight_megapixels() -> None:
    assert Settings().recognition_upscale_target_pixels == 8_000_000
    assert QWEN_DATA_URI_MAX_BYTES == 20_000_000


def test_qwen_request_streams_high_resolution_thinking_without_temperature(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict = {}

    class FakeChunk:
        def __init__(self, *, content: str = "", reasoning: str = "", usage: dict | None = None):
            self.choices = (
                [
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=content,
                            reasoning_content=reasoning,
                        )
                    )
                ]
                if content or reasoning
                else []
            )
            self.usage = usage
            self._usage = usage

        def model_dump(self, mode: str = "json") -> dict:
            del mode
            return {"usage": self._usage} if self._usage else {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return iter(
                [
                    FakeChunk(reasoning="private reasoning"),
                    FakeChunk(content='{"recognition_notes":""}'),
                    FakeChunk(
                        usage={
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                            "input_tokens_details": {"image_tokens": 80},
                            "output_tokens_details": {"reasoning_tokens": 30},
                        }
                    ),
                ]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    settings = Settings(repository_root=REPOSITORY_ROOT, data_dir=tmp_path)
    context = RecognitionContext(
        payload_model=build_payload_model(
            _prior(),
            RuleProfileId.FIBA_2024,
            REPOSITORY_ROOT / "shared" / "rule_profiles.json",
        ),
        system_prompt="system",
        user_prompt="user",
        schema={"type": "object"},
        image_bytes=b"image",
        image_data_url="data:image/jpeg;base64,aW1hZ2U=",
        cache_key="cache",
    )
    phases: list[str] = []

    result = QwenRecognitionProvider(settings).recognize(
        context=context,
        prior=_prior(),
        model="qwen3.8-max",
        progress=phases.append,
    )

    assert result.payload == {"recognition_notes": ""}
    assert result.usage.total_tokens == 150
    assert result.usage.image_tokens == 80
    assert result.usage.reasoning_tokens == 30
    assert phases == ["thinking", "structuring"]
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert "temperature" not in captured
    assert "max_tokens" not in captured
    assert captured["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "xhigh",
        "vl_high_resolution_images": True,
        "preserve_thinking": False,
    }


def test_schema_rejects_impossible_three_digit_jersey_numbers() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    payload = {
        "team_a": {
            "players": [
                {
                    "row": 1,
                    "name": "张三",
                    "jersey_number": "123",
                    "captain": False,
                    "participation": "starter",
                    "fouls": [],
                }
            ],
            "timeouts": [],
            "team_fouls": [],
            "head_coach": {"name": None, "fouls": []},
            "assistant_coach": {"name": None, "fouls": []},
            "running_score": [],
        },
        "team_b": {
            "players": [],
            "timeouts": [],
            "team_fouls": [],
            "head_coach": {"name": None, "fouls": []},
            "assistant_coach": {"name": None, "fouls": []},
            "running_score": [],
        },
        "period_scores": [{"period": period, "team_a": 0, "team_b": 0} for period in range(1, 5)],
        "final_score": {"team_a": None, "team_b": None, "winner_name": None, "ended_at": None},
        "table_personnel": [],
        "officials": [],
        "recognition_notes": "",
    }

    with pytest.raises(ValidationError):
        model.model_validate(payload)

    payload["team_a"]["players"][0]["jersey_number"] = "07"
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_schema_outputs_summary_before_teams_for_score_cross_check() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )

    assert list(model.model_json_schema()["properties"])[:4] == [
        "period_scores",
        "final_score",
        "team_a",
        "team_b",
    ]


def test_schema_separates_unassigned_table_personnel_from_role_bound_officials() -> None:
    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    schema = model.model_json_schema()
    role_schema = schema["$defs"]["RecognizedOfficial"]["properties"]["role"]

    assert schema["properties"]["table_personnel"]["items"] == {"type": "string"}
    assert role_schema["enum"] == ["crew_chief", "umpire_1", "umpire_2", "protest_captain"]


def test_unknown_provider_names_become_null_without_fuzzy_matching() -> None:
    payload = {
        "team_a": {"players": [{"row": 3, "name": "张山"}]},
        "team_b": {"players": [{"row": 5, "name": "王五"}]},
        "recognition_notes": "号码不清晰。",
    }

    sanitized = sanitize_unknown_player_names(payload, _prior())

    assert sanitized["team_a"]["players"][0]["name"] is None
    assert sanitized["team_b"]["players"][0]["name"] == "王五"
    assert "A队第3行姓名不在唯一名单中，已置空" in sanitized["recognition_notes"]
    assert "张山" not in sanitized["recognition_notes"]


def test_provider_payload_only_unwraps_a_single_schema_object() -> None:
    payload = {"recognition_notes": ""}

    assert normalize_provider_payload(payload) is payload
    assert normalize_provider_payload([payload]) is payload
    with pytest.raises(RecognitionProviderError):
        normalize_provider_payload([])
    with pytest.raises(RecognitionProviderError):
        normalize_provider_payload([payload, payload])


def test_sparse_running_score_normalizes_numeric_jerseys_without_changing_events() -> None:
    payload = _valid_payload()
    payload["team_b"]["running_score"] = [
        {"cumulative_score": 91, "scorer_jersey": 13, "points": 2}
    ]

    normalized, issues = normalize_running_score_payload(payload)

    assert "running_score_rows" not in normalized
    assert normalized["team_b"]["running_score"] == [
        {"cumulative_score": 91, "scorer_jersey": "13", "points": 2}
    ]
    assert {issue.code for issue in issues} == {"RUNNING_SCORE_JERSEY_NORMALIZED"}

    model = build_payload_model(
        _prior(),
        RuleProfileId.FIBA_2024,
        REPOSITORY_ROOT / "shared" / "rule_profiles.json",
    )
    model.model_validate(normalized)


def test_v23_grid_is_downgraded_to_sparse_running_score_for_compatibility() -> None:
    payload = _valid_payload()
    payload["team_a"].pop("running_score")
    payload["team_b"].pop("running_score")
    payload["running_score_rows"] = [
        {
            "cumulative_score": cumulative,
            "team_a": {"scorer_jersey": None, "points": None, "has_score_mark": False},
            "team_b": {"scorer_jersey": None, "points": None, "has_score_mark": False},
        }
        for cumulative in range(1, 161)
    ]
    payload["running_score_rows"][1]["team_a"] = {
        "scorer_jersey": "7",
        "points": None,
        "has_score_mark": True,
    }

    normalized, issues = normalize_running_score_payload(payload)

    assert "running_score_rows" not in normalized
    assert normalized["team_a"]["running_score"] == [
        {"cumulative_score": 2, "scorer_jersey": "7", "points": 2}
    ]
    assert normalized["team_b"]["running_score"] == []
    assert {"RUNNING_SCORE_POINTS_DERIVED", "RUNNING_SCORE_LEGACY_GRID_CONVERTED"} <= {
        issue.code for issue in issues
    }


def _wait_for_run(client, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/v1/recognitions/{run_id}").json()
        if run["status"] in {"succeeded", "failed", "superseded", "interrupted"}:
            return run
        time.sleep(0.01)
    raise AssertionError("recognition did not finish")


def test_mock_recognition_starts_on_upload_and_applies_to_editor_document(
    recognition_client,
    sample_png: bytes,
) -> None:
    games = recognition_client.get("/api/v1/games").json()
    assert len(games) == 1
    assert games[0]["ready"] is True
    assert games[0]["document_id"] is None
    assert games[0]["scoresheet_state"] == "not_uploaded"
    detail = recognition_client.get(f"/api/v1/games/{games[0]['id']}").json()
    assert detail["prior"]["team_a"]["player_names"][0] == "甲队员一"
    assert "jersey" not in json.dumps(detail, ensure_ascii=False).lower()

    created = recognition_client.post(
        f"/api/v1/games/{games[0]['id']}/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    )
    assert created.status_code == 201
    upload = created.json()
    document = upload["document"]
    assert document["header"]["competition"] == "公开合成测试赛"
    assert document["header"]["game_number"] == ""
    assert document["game_prior"]["team_a"]["player_names"][0] == "甲队员一"
    uploaded_game = recognition_client.get("/api/v1/games").json()[0]
    assert uploaded_game["document_id"] == document["id"]
    assert uploaded_game["scoresheet_state"] in {"recognizing", "recognized"}

    run = _wait_for_run(recognition_client, upload["recognition_run"]["id"])
    assert run["status"] == "succeeded"
    assert run["trigger"] == "upload"
    assert run["cached"] is False
    assert run["auto_applied"] is True
    assert run["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "image_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    assert "running_score_rows" not in run["result"]
    assert run["result"]["team_a"]["running_score"] == [
        {"cumulative_score": 2, "scorer_jersey": "4", "points": 2},
        {"cumulative_score": 3, "scorer_jersey": "5", "points": 1},
    ]
    recognized_game = recognition_client.get("/api/v1/games").json()[0]
    assert recognized_game["document_id"] == document["id"]
    assert recognized_game["scoresheet_state"] == "recognized"

    recognized = recognition_client.get(f"/api/v1/documents/{document['id']}").json()
    assert recognized["revision"] == 1
    assert recognized["status"] == "needs_review"
    assert recognized["teams"][0]["players"][0]["name"] == "甲队员一"
    assert [event["points"] for event in recognized["score_events"]] == [2, 1, 2]
    assert recognized["recognition"]["run_id"] == run["id"]
    assert recognized["recognition"]["table_personnel"] == [
        "示例记录台人员甲",
        "示例记录台人员乙",
    ]
    table_roles = {"scorer", "assistant_scorer", "timer", "shot_clock_operator"}
    assert all(
        not official["name"]
        for official in recognized["officials"]
        if official["role"] in table_roles
    )

    rerun = recognition_client.post(
        f"/api/v1/documents/{document['id']}/recognitions",
        json={"base_revision": recognized["revision"]},
    )
    assert rerun.status_code == 409
    assert "不支持重复识别" in rerun.json()["detail"]
    changes = recognition_client.get(f"/api/v1/documents/{document['id']}/changes").json()
    assert changes["items"] == []


def test_image_preparation_failure_is_reported_without_calling_provider(
    recognition_client,
    sample_png: bytes,
    monkeypatch,
) -> None:
    service = recognition_client.app.state.recognition_service
    provider_calls = 0

    def should_not_call_provider(**kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run after image preparation fails")

    def fail_preparation(*args, **kwargs):
        raise RecognitionProviderError(
            "图片在不缩小分辨率的条件下无法满足 Qwen 20 MB Base64 Data URI 限制。"
        )

    service.provider.recognize = should_not_call_provider
    monkeypatch.setattr("scoresheet_reader.recognition._prepare_image", fail_preparation)
    game = recognition_client.get("/api/v1/games").json()[0]

    response = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    )

    assert response.status_code == 201
    run = response.json()["recognition_run"]
    assert run["status"] == "failed"
    assert "20 MB Base64 Data URI" in run["error"]
    assert run["usage"]["total_tokens"] == 0
    assert provider_calls == 0
    latest = recognition_client.get(
        f"/api/v1/documents/{response.json()['document']['id']}/recognitions/latest"
    ).json()
    assert latest["id"] == run["id"]
    assert latest["status"] == "failed"


def test_reuploading_identical_bytes_forces_a_new_provider_call(
    recognition_client,
    sample_png: bytes,
) -> None:
    service = recognition_client.app.state.recognition_service
    original_recognize = service.provider.recognize
    call_count = 0

    def counted_recognize(**kwargs):
        nonlocal call_count
        call_count += 1
        return original_recognize(**kwargs)

    service.provider.recognize = counted_recognize
    game = recognition_client.get("/api/v1/games").json()[0]
    first = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("same.png", sample_png, "image/png")},
    ).json()
    first_run = _wait_for_run(recognition_client, first["recognition_run"]["id"])
    first_document = recognition_client.get(f"/api/v1/documents/{first['document']['id']}").json()

    second = recognition_client.put(
        f"/api/v1/documents/{first_document['id']}/source",
        data={"base_revision": first_document["revision"]},
        files={"file": ("same.png", sample_png, "image/png")},
    ).json()
    second_run = _wait_for_run(recognition_client, second["recognition_run"]["id"])

    assert call_count == 2
    assert first_run["id"] != second_run["id"]
    assert first_run["image_sha256"] == second_run["image_sha256"]
    assert second_run["trigger"] == "reupload"
    assert second_run["cached"] is False
    assert second["document"]["source"]["version"] == 1


def test_reupload_supersedes_inflight_run_and_each_run_reads_its_own_image(
    recognition_client,
    sample_png: bytes,
) -> None:
    service = recognition_client.app.state.recognition_service
    original_recognize = service.provider.recognize
    first_started = Event()
    release_first = Event()
    seen_images: list[str] = []

    def blocking_recognize(**kwargs):
        seen_images.append(hashlib.sha256(kwargs["context"].image_bytes).hexdigest())
        if len(seen_images) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        return original_recognize(**kwargs)

    service.provider.recognize = blocking_recognize
    game = recognition_client.get("/api/v1/games").json()[0]
    first = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("first.png", sample_png, "image/png")},
    ).json()
    assert first_started.wait(timeout=2)

    replacement_image = Image.new("RGB", (480, 680), "#d6e8f1")
    replacement_buffer = io.BytesIO()
    replacement_image.save(replacement_buffer, format="PNG")
    second = recognition_client.put(
        f"/api/v1/documents/{first['document']['id']}/source",
        data={"base_revision": first["document"]["revision"]},
        files={
            "file": ("second.png", replacement_buffer.getvalue(), "image/png"),
        },
    ).json()
    first_during_reupload = recognition_client.get(
        f"/api/v1/recognitions/{first['recognition_run']['id']}"
    ).json()
    assert first_during_reupload["superseded_by_run_id"] == second["recognition_run"]["id"]
    assert second["recognition_run"]["status"] == "pending"

    release_first.set()
    first_terminal = _wait_for_run(recognition_client, first["recognition_run"]["id"])
    second_terminal = _wait_for_run(recognition_client, second["recognition_run"]["id"])
    final_document = recognition_client.get(f"/api/v1/documents/{first['document']['id']}").json()

    assert first_terminal["status"] == "superseded"
    assert second_terminal["status"] == "succeeded"
    assert len(seen_images) == 2
    assert seen_images[0] != seen_images[1]
    assert final_document["recognition"]["run_id"] == second_terminal["id"]


def test_failed_automatic_recognition_can_be_retried_once_from_the_editor(
    recognition_client,
    sample_png: bytes,
) -> None:
    service = recognition_client.app.state.recognition_service
    original_recognize = service.provider.recognize
    calls = 0

    def fail_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RecognitionProviderError("mock transport failure")
        return original_recognize(**kwargs)

    service.provider.recognize = fail_once
    game = recognition_client.get("/api/v1/games").json()[0]
    uploaded = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("retry.png", sample_png, "image/png")},
    ).json()
    first = _wait_for_run(recognition_client, uploaded["recognition_run"]["id"])
    assert first["status"] == "failed"
    assert recognition_client.get("/api/v1/games").json()[0]["scoresheet_state"] == (
        "recognition_failed"
    )

    retried = recognition_client.post(
        f"/api/v1/documents/{uploaded['document']['id']}/recognitions",
        json={"base_revision": uploaded["document"]["revision"]},
    )
    assert retried.status_code == 202
    terminal = _wait_for_run(recognition_client, retried.json()["id"])

    assert calls == 2
    assert terminal["status"] == "succeeded"
    assert terminal["trigger"] == "retry"
    assert terminal["cached"] is False
