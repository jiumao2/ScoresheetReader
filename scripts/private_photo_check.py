from __future__ import annotations

import argparse
import io
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from scoresheet_reader.api import create_app
from scoresheet_reader.database import DocumentRepository
from scoresheet_reader.settings import REPOSITORY_ROOT, Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise private photo upload/display compatibility without model calls."
    )
    parser.add_argument("--input", type=Path, default=REPOSITORY_ROOT / "test")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "private_test" / "photo_check.json",
    )
    return parser.parse_args()


def image_metadata(payload: bytes) -> dict[str, object]:
    with Image.open(io.BytesIO(payload)) as image:
        image.verify()
    with Image.open(io.BytesIO(payload)) as image:
        return {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "exif_orientation": image.getexif().get(274, 1),
        }


def main() -> int:
    args = parse_args()
    supported_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    images = sorted(
        path
        for path in args.input.iterdir()
        if path.is_file() and path.suffix.lower() in supported_suffixes
    )
    if not images:
        raise SystemExit(f"No private images found under {args.input}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="scoresheet-private-check-") as temp:
        settings = Settings(
            repository_root=REPOSITORY_ROOT,
            template_path=REPOSITORY_ROOT / "scoresheet_template.pdf",
            data_dir=Path(temp),
        )
        repository = DocumentRepository(settings.database_path)
        app = create_app(settings, repository)
        try:
            with TestClient(app) as client:
                for path in images:
                    payload = path.read_bytes()
                    source_metadata = image_metadata(payload)
                    response = client.post(
                        "/api/v1/documents",
                        files={"file": (path.name, payload, "image/jpeg")},
                    )
                    response.raise_for_status()
                    document = response.json()
                    normalized_response = client.get(document["source"]["original_url"])
                    normalized_response.raise_for_status()
                    normalized_metadata = image_metadata(normalized_response.content)

                    alignment = client.post(
                        f"/api/v1/documents/{document['id']}/alignment",
                        json={
                            "base_revision": document["revision"],
                            "rotation": 0,
                            "corners": [
                                [0.0, 0.0],
                                [1.0, 0.0],
                                [1.0, 1.0],
                                [0.0, 1.0],
                            ],
                        },
                    )
                    alignment.raise_for_status()
                    aligned_document = alignment.json()
                    aligned_response = client.get(
                        aligned_document["source"]["aligned_url"]
                    )
                    aligned_response.raise_for_status()
                    aligned_metadata = image_metadata(aligned_response.content)

                    results.append(
                        {
                            "filename": path.name,
                            "upload": source_metadata,
                            "normalized": normalized_metadata,
                            "aligned": aligned_metadata,
                            "revision": aligned_document["revision"],
                            "status": "passed",
                        }
                    )
        finally:
            repository.engine.dispose()

    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "input_directory": str(args.input),
        "files_checked": len(results),
        "recognition_calls": 0,
        "model_cost": 0,
        "results": results,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
