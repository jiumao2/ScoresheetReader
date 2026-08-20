from __future__ import annotations

import io

from PIL import Image


def test_health_reports_on_demand_recognition_without_touching_a_key(client) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "recognition": "on_demand",
        "master_data": "empty",
    }


def test_corrupt_image_returns_a_client_error_instead_of_server_error(client) -> None:
    response = client.post(
        "/api/v1/documents",
        files={"file": ("broken.png", b"\x89PNG\r\n\x1a\ninvalid", "image/png")},
    )

    assert response.status_code == 415
    assert "JPEG、PNG 或 WebP" in response.json()["detail"]


def test_exif_orientation_is_normalized_before_display_and_alignment(client) -> None:
    source = Image.new("RGB", (80, 40), "white")
    exif = source.getexif()
    exif[274] = 8
    payload = io.BytesIO()
    source.save(payload, format="JPEG", exif=exif)

    created = client.post(
        "/api/v1/documents",
        files={"file": ("phone.jpg", payload.getvalue(), "image/jpeg")},
    )
    assert created.status_code == 201
    document = created.json()
    assert (document["source"]["width"], document["source"]["height"]) == (40, 80)

    displayed = client.get(document["source"]["original_url"])
    with Image.open(io.BytesIO(displayed.content)) as image:
        assert image.size == (40, 80)
        assert image.getexif().get(274, 1) == 1


def test_upload_alignment_save_and_revision_conflict(client, sample_png: bytes) -> None:
    created = client.post(
        "/api/v1/documents",
        files={"file": ("sheet.png", sample_png, "image/png")},
    )
    assert created.status_code == 201
    document = created.json()
    document_id = document["id"]
    assert document["source"]["width"] == 480
    assert client.get(document["source"]["original_url"]).status_code == 200

    document["header"]["competition"] = "本地测试赛"
    saved = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    conflict = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"base_revision": 0, "document": document, "source": "human"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"

    aligned = client.post(
        f"/api/v1/documents/{document_id}/alignment",
        json={
            "base_revision": 1,
            "rotation": 90,
            "corners": [[0.02, 0.02], [0.98, 0.02], [0.98, 0.98], [0.02, 0.98]],
        },
    )
    assert aligned.status_code == 200
    assert aligned.json()["revision"] == 2
    assert client.get(f"/api/v1/documents/{document_id}/source?aligned=true").status_code == 200

    revisions = client.get(f"/api/v1/documents/{document_id}/revisions").json()
    assert [entry["revision"] for entry in revisions] == [2, 1, 0]
    assert [entry["source"] for entry in revisions] == ["alignment", "human", "upload"]


def test_fixture_validate_confirm_and_export(client) -> None:
    created = client.post("/api/v1/fixtures/synthetic")
    assert created.status_code == 201
    document = created.json()
    document_id = document["id"]

    report = client.post(f"/api/v1/documents/{document_id}/validate")
    assert report.status_code == 200
    assert report.json()["status"] == "valid"

    confirmed = client.post(
        f"/api/v1/documents/{document_id}/confirm",
        json={"base_revision": 0, "acknowledge_warning_codes": []},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["revision"] == 1

    svg = client.get(f"/api/v1/documents/{document_id}/render.svg")
    pdf = client.get(f"/api/v1/documents/{document_id}/render.pdf")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert b"score.A.003.mark" in svg.content
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


def test_errors_block_confirmation_and_warnings_require_acknowledgement(client) -> None:
    created = client.post("/api/v1/fixtures/synthetic").json()
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
