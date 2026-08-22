from __future__ import annotations

import asyncio
import io
import time

from PIL import Image

from scoresheet_reader import api as api_module

from .synthetic_fixture import synthetic_document


def _game_upload_url(client) -> str:
    game = next(item for item in client.get("/api/v1/games").json() if item["ready"])
    return f"/api/v1/games/{game['id']}/documents"


def _wait_for_recognition(client, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/v1/recognitions/{run_id}").json()
        if run["status"] in {"succeeded", "failed", "superseded", "interrupted"}:
            return run
        time.sleep(0.01)
    raise AssertionError("recognition did not finish")


def _create_test_document(client):
    return client.app.state.repository.create(synthetic_document(), source="test_setup")


def test_health_reports_automatic_recognition_without_touching_a_key(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "recognition": "automatic",
        "master_data": "empty",
    }


def test_direct_upload_without_a_game_prior_is_not_exposed(client, sample_png: bytes) -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    )

    assert response.status_code == 404


def test_corrupt_image_returns_a_client_error_instead_of_server_error(recognition_client) -> None:
    response = recognition_client.post(
        _game_upload_url(recognition_client),
        files={"file": ("broken.png", b"\x89PNG\r\n\x1a\ninvalid", "image/png")},
    )

    assert response.status_code == 415
    assert "JPEG、PNG 或 WebP" in response.json()["detail"]


def test_upload_preserves_pillow_decompression_bomb_protection(
    recognition_client,
    sample_png: bytes,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)

    response = recognition_client.post(
        _game_upload_url(recognition_client),
        files={"file": ("large.png", sample_png, "image/png")},
    )

    assert response.status_code == 413
    assert "Pillow 安全保护" in response.json()["detail"]


def test_upload_accepts_images_above_the_removed_40_megapixel_limit(app_settings) -> None:
    source = Image.new("1", (6500, 6500), 1)
    payload = io.BytesIO()
    source.save(payload, format="PNG")
    upload = api_module.UploadFile(filename="large.png", file=io.BytesIO(payload.getvalue()))

    _, asset, stored_path = asyncio.run(api_module._store_upload(upload, app_settings))

    assert asset.width * asset.height == 42_250_000
    assert stored_path.exists()


def test_upload_streams_files_larger_than_the_removed_25_mb_limit(
    app_settings,
    sample_png: bytes,
) -> None:
    legacy_limit = 25 * 1024 * 1024

    class GeneratedUpload:
        filename = "large.png"

        def __init__(self) -> None:
            self.offset = 0
            self.total_size = legacy_limit + 1
            self.read_sizes: list[int] = []

        async def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            if self.offset >= self.total_size:
                return b""
            chunk_size = min(size, self.total_size - self.offset)
            start = self.offset
            self.offset += chunk_size
            if start < len(sample_png):
                prefix = sample_png[start : start + chunk_size]
                return prefix + bytes(chunk_size - len(prefix))
            return bytes(chunk_size)

    upload = GeneratedUpload()
    _, _, stored_path = asyncio.run(api_module._store_upload(upload, app_settings))

    assert stored_path.stat().st_size == legacy_limit + 1
    assert set(upload.read_sizes) == {api_module.UPLOAD_CHUNK_BYTES}


def test_exif_orientation_is_normalized_before_display_and_alignment(recognition_client) -> None:
    source = Image.new("RGB", (80, 40), "white")
    exif = source.getexif()
    exif[274] = 8
    payload = io.BytesIO()
    source.save(payload, format="JPEG", exif=exif)

    created = recognition_client.post(
        _game_upload_url(recognition_client),
        files={"file": ("phone.jpg", payload.getvalue(), "image/jpeg")},
    )
    assert created.status_code == 201
    document = created.json()["document"]
    assert (document["source"]["width"], document["source"]["height"]) == (40, 80)

    displayed = recognition_client.get(document["source"]["original_url"])
    with Image.open(io.BytesIO(displayed.content)) as image:
        assert image.size == (40, 80)
        assert image.getexif().get(274, 1) == 1


def test_upload_alignment_save_and_revision_conflict(
    recognition_client,
    sample_png: bytes,
) -> None:
    created = recognition_client.post(
        _game_upload_url(recognition_client),
        files={"file": ("sheet.png", sample_png, "image/png")},
    )
    assert created.status_code == 201
    created_payload = created.json()
    _wait_for_recognition(recognition_client, created_payload["recognition_run"]["id"])
    document = recognition_client.get(
        f"/api/v1/documents/{created_payload['document']['id']}"
    ).json()
    document_id = document["id"]
    assert document["source"]["width"] == 480
    assert recognition_client.get(document["source"]["original_url"]).status_code == 200

    base_revision = document["revision"]
    document["header"]["game_number"] = "2"
    saved = recognition_client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": base_revision, "document": document, "source": "human"},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == base_revision + 1

    conflict = recognition_client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": base_revision, "document": document, "source": "human"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"

    aligned = recognition_client.post(
        f"/api/v1/documents/{document_id}/alignment",
        json={
            "base_revision": base_revision + 1,
            "rotation": 90,
            "corners": [[0.02, 0.02], [0.98, 0.02], [0.98, 0.98], [0.02, 0.98]],
        },
    )
    assert aligned.status_code == 200
    assert aligned.json()["revision"] == base_revision + 2
    assert recognition_client.get(aligned.json()["source"]["aligned_url"]).status_code == 200

    changes = recognition_client.get(f"/api/v1/documents/{document_id}/changes").json()
    assert [entry["action"] for entry in changes["items"]] == ["human_edit"]
    assert changes["items"][0]["changes"] == [
        {"path": "/header/game_number", "before": "", "after": "2"}
    ]
    assert "document" not in changes["items"][0]
    assert "revision" not in changes["items"][0]


def test_production_fixture_routes_are_not_exposed(client) -> None:
    assert client.get("/api/v1/fixtures/synthetic").status_code == 404
    assert client.post("/api/v1/fixtures/synthetic").status_code == 404


def test_internal_fixture_validate_confirm_and_export(client) -> None:
    document = _create_test_document(client).model_dump(mode="json")
    document_id = document["id"]

    report = client.post(
        f"/api/v1/documents/{document_id}/validate",
        json={"base_revision": 0},
    )
    assert report.status_code == 200
    assert report.json()["status"] == "valid"

    confirmed = client.post(
        f"/api/v1/documents/{document_id}/confirm",
        json={"base_revision": 0, "acknowledge_warning_codes": []},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["revision"] == 1
    change_log = client.get(f"/api/v1/documents/{document_id}/changes").json()
    assert [entry["action"] for entry in change_log["items"]] == ["confirm"]

    svg = client.get(f"/api/v1/documents/{document_id}/render.svg")
    pdf = client.get(f"/api/v1/documents/{document_id}/render.pdf")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert b"score.A.003.mark" in svg.content
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_generic_patch_cannot_bypass_confirmation_or_change_server_owned_fields(client) -> None:
    document = _create_test_document(client).model_dump(mode="json")
    document_id = document["id"]

    document["status"] = "confirmed"
    bypass = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert bypass.status_code == 422
    assert bypass.json()["detail"]["field"] == "status"

    document["status"] = "draft"
    document["source"]["original_filename"] = "forged.png"
    source_tamper = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert source_tamper.status_code == 422
    assert source_tamper.json()["detail"]["field"] == "source"

    document["source"] = client.get(f"/api/v1/documents/{document_id}").json()["source"]
    document["acknowledged_warnings"] = ["FORGED_WARNING"]
    sanitized = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert sanitized.status_code == 200
    assert sanitized.json()["acknowledged_warnings"] == []
    assert sanitized.json()["status"] == "draft"


def test_generic_patch_cannot_change_game_prior_or_recognition_identity(
    recognition_client,
    sample_png: bytes,
) -> None:
    game = next(item for item in recognition_client.get("/api/v1/games").json() if item["ready"])
    uploaded = recognition_client.post(
        f"/api/v1/games/{game['id']}/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    ).json()
    _wait_for_recognition(recognition_client, uploaded["recognition_run"]["id"])
    document = recognition_client.get(f"/api/v1/documents/{uploaded['document']['id']}").json()
    document["game_prior"]["competition"] = "伪造比赛"
    prior_tamper = recognition_client.patch(
        f"/api/v1/documents/{document['id']}",
        json={
            "base_revision": document["revision"],
            "document": document,
            "source": "human",
        },
    )
    assert prior_tamper.status_code == 422
    assert prior_tamper.json()["detail"]["field"] == "game_prior"

    recognized = recognition_client.get(f"/api/v1/documents/{document['id']}").json()
    assert recognized["recognition"] is not None
    recognized["recognition"]["run_id"] = "forged-run"
    recognition_tamper = recognition_client.patch(
        f"/api/v1/documents/{document['id']}",
        json={
            "base_revision": recognized["revision"],
            "document": recognized,
            "source": "human",
        },
    )
    assert recognition_tamper.status_code == 422
    assert recognition_tamper.json()["detail"]["field"] == "recognition.run_id"


def test_validation_rejects_a_stale_revision(client) -> None:
    document = _create_test_document(client).model_dump(mode="json")
    document_id = document["id"]
    document["header"]["game_number"] = "2"
    saved = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert saved.status_code == 200

    response = client.post(
        f"/api/v1/documents/{document_id}/validate",
        json={"base_revision": 0},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REVISION_CONFLICT"


def test_errors_block_confirmation_and_warnings_require_acknowledgement(client) -> None:
    created = _create_test_document(client).model_dump(mode="json")
    document_id = created["id"]
    created["final_score"]["winner_name"] = "错误胜队"
    saved = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": created, "source": "human"},
    ).json()
    blocked = client.post(
        f"/api/v1/documents/{document_id}/confirm",
        json={"base_revision": saved["revision"], "acknowledge_warning_codes": []},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert any(
        issue["code"] == "WINNER_MISMATCH" for issue in blocked.json()["detail"]["report"]["issues"]
    )

    saved["final_score"]["winner_name"] = saved["teams"][0]["name"]
    saved["header"]["venue"] = ""
    warning_doc = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": saved["revision"], "document": saved, "source": "human"},
    ).json()
    warning_blocked = client.post(
        f"/api/v1/documents/{document_id}/confirm",
        json={"base_revision": warning_doc["revision"], "acknowledge_warning_codes": []},
    )
    warning_code = "MISSING_VENUE"
    assert warning_blocked.status_code == 409
    assert warning_code in warning_blocked.json()["detail"]["unacknowledged_warnings"]

    confirmed = client.post(
        f"/api/v1/documents/{document_id}/confirm",
        json={
            "base_revision": warning_doc["revision"],
            "acknowledge_warning_codes": [warning_code],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["acknowledged_warnings"] == [warning_code]
