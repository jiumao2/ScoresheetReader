from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from scoresheet_reader.api import create_app
from scoresheet_reader.database import DocumentRepository
from scoresheet_reader.settings import REPOSITORY_ROOT, Settings


@pytest.fixture
def blank_template(tmp_path: Path) -> Path:
    path = tmp_path / "template.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595.32, height=842.04)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


@pytest.fixture
def app_settings(tmp_path: Path, blank_template: Path) -> Settings:
    return Settings(
        repository_root=REPOSITORY_ROOT,
        template_path=blank_template,
        data_dir=tmp_path / "data",
        master_data_dir=tmp_path / "master-data",
    )


@pytest.fixture
def client(app_settings: Settings) -> TestClient:
    repository = DocumentRepository(app_settings.database_path)
    with TestClient(create_app(app_settings, repository)) as test_client:
        yield test_client


@pytest.fixture
def recognition_client(tmp_path: Path, blank_template: Path) -> TestClient:
    settings = Settings(
        repository_root=REPOSITORY_ROOT,
        template_path=blank_template,
        data_dir=tmp_path / "recognition-data",
        master_data_dir=tmp_path / "unused-master-data",
        master_fixture_path=REPOSITORY_ROOT / "shared" / "demo_master_data.json",
        recognition_mode="mock",
    )
    repository = DocumentRepository(settings.database_path)
    with TestClient(create_app(settings, repository)) as test_client:
        yield test_client


@pytest.fixture
def sample_png() -> bytes:
    image = Image.new("RGB", (480, 680), "#eee9df")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
