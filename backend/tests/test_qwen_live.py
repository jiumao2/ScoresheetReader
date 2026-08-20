from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from scoresheet_reader.api import create_app
from scoresheet_reader.database import DocumentRepository
from scoresheet_reader.recognition import PROMPT_VERSION
from scoresheet_reader.recognition_eval import evaluate_recognition
from scoresheet_reader.settings import REPOSITORY_ROOT, Settings


@pytest.mark.skipif(
    os.getenv("RUN_QWEN_LIVE") != "1",
    reason="付费测试只在显式设置 RUN_QWEN_LIVE=1 时运行。",
)
def test_private_math_foreign_languages_sheet_once() -> None:
    """Run exactly one paid whole-image request against the private reference sheet."""
    image_path = REPOSITORY_ROOT / "test" / "20260321_男甲_数学_VS_外院_2.jpg"
    if not image_path.exists():
        pytest.skip("私有数学 vs 外院记录表不存在。")
    if not os.getenv("QWEN_API_KEY"):
        pytest.fail("RUN_QWEN_LIVE=1 时必须提供 QWEN_API_KEY。")

    settings = Settings(
        repository_root=REPOSITORY_ROOT,
        template_path=REPOSITORY_ROOT / "scoresheet_template.pdf",
        data_dir=REPOSITORY_ROOT / "private_test" / "qwen-live-data",
        master_data_dir=REPOSITORY_ROOT / "test",
        recognition_mode="qwen",
        qwen_model="qwen3.8-max",
    )
    repository = DocumentRepository(settings.database_path)
    app = create_app(settings, repository)
    try:
        with TestClient(app) as client:
            games = client.get("/api/v1/games").json()
            game = next(
                item
                for item in games
                if item["ready"]
                and "数学" in item["team_a_name"] + item["team_b_name"]
                and "外院" in item["team_a_name"] + item["team_b_name"]
            )
            with image_path.open("rb") as image:
                document_response = client.post(
                    f"/api/v1/games/{game['id']}/documents",
                    files={"file": (image_path.name, image, "image/jpeg")},
                )
            document_response.raise_for_status()
            document = document_response.json()

            # There is deliberately one recognition creation and no paid retry.
            run_response = client.post(
                f"/api/v1/documents/{document['id']}/recognitions",
                json={"base_revision": document["revision"]},
            )
            run_response.raise_for_status()
            run = client.get(f"/api/v1/recognitions/{run_response.json()['id']}").json()
            audit_dir = REPOSITORY_ROOT / "private_test"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit = dict(run)
            truth_path = audit_dir / "math_vs_foreign.ground_truth.json"
            if run["status"] == "succeeded" and truth_path.exists():
                truth = json.loads(truth_path.read_text(encoding="utf-8"))
                audit["evaluation"] = evaluate_recognition(run["result"], truth)
            audit_json = json.dumps(audit, ensure_ascii=False, indent=2)
            for audit_path in (
                audit_dir / "qwen_live_last.json",
                audit_dir / f"qwen_live_{PROMPT_VERSION}.json",
            ):
                audit_path.write_text(audit_json, encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": run["status"],
                        "cached": run["cached"],
                        "qwen_usage": run["usage"],
                        "evaluation": audit.get("evaluation"),
                        "error": run["error"],
                    },
                    ensure_ascii=True,
                )
            )
            assert run["status"] == "succeeded", run.get("error")
            assert run["model"] == "qwen3.8-max"
            if not run["cached"]:
                assert run["usage"]["total_tokens"] > 0
    finally:
        repository.engine.dispose()
