from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    repository_root: Path = REPOSITORY_ROOT
    template_path: Path = Path(
        os.getenv("SCORESHEET_TEMPLATE_PATH", REPOSITORY_ROOT / "scoresheet_template.pdf")
    )
    data_dir: Path = Path(os.getenv("SCORESHEET_DATA_DIR", REPOSITORY_ROOT / "data"))
    font_path: Path | None = (
        Path(os.environ["SCORESHEET_FONT_PATH"]) if os.getenv("SCORESHEET_FONT_PATH") else None
    )
    master_data_dir: Path | None = (
        Path(os.environ["SCORESHEET_MASTER_DATA_DIR"])
        if os.getenv("SCORESHEET_MASTER_DATA_DIR")
        else None
    )
    master_fixture_path: Path | None = (
        Path(os.environ["SCORESHEET_MASTER_FIXTURE_PATH"])
        if os.getenv("SCORESHEET_MASTER_FIXTURE_PATH")
        else None
    )
    competition_name: str = os.getenv("SCORESHEET_COMPETITION_NAME", "2026北大杯")
    recognition_mode: str = os.getenv("SCORESHEET_RECOGNITION_MODE", "qwen")
    qwen_base_url: str = os.getenv(
        "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    qwen_model: str = os.getenv("QWEN_MODEL", "qwen3.8-max")
    qwen_reasoning_effort: str = os.getenv("QWEN_REASONING_EFFORT", "xhigh")
    recognition_upscale_target_pixels: int = int(
        os.getenv("SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS", "8000000")
    )
    recognition_timeout_seconds: float = float(
        os.getenv("SCORESHEET_RECOGNITION_TIMEOUT_SECONDS", "180")
    )
    recognition_concurrency: int = int(os.getenv("SCORESHEET_RECOGNITION_CONCURRENCY", "2"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "scoresheet_reader.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def template_definition_path(self) -> Path:
        return self.repository_root / "shared" / "template_definition.json"

    @property
    def rule_profiles_path(self) -> Path:
        return self.repository_root / "shared" / "rule_profiles.json"

    @property
    def resolved_master_data_dir(self) -> Path:
        return self.master_data_dir or self.repository_root / "test"

    @property
    def resolved_master_fixture_path(self) -> Path | None:
        return self.master_fixture_path

    def qwen_api_key(self) -> str:
        """Read the paid-provider secret only when a live recognition is executed."""
        return os.getenv("QWEN_API_KEY", "")

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
