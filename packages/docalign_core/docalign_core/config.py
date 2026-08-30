from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DOCALIGN_",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/docalign.db"
    max_upload_mb: int = Field(default=20, ge=1)
    max_rule_pack_import_kb: int = Field(default=2_048, ge=64, le=10_240)
    max_batch_files: int = Field(default=20, ge=2, le=100)
    max_batch_total_mb: int = Field(default=200, ge=20, le=2_000)
    max_uncompressed_mb: int = Field(default=200, ge=1)
    max_zip_entries: int = Field(default=10_000, ge=100)
    max_compression_ratio: float = Field(default=100, ge=1)
    job_concurrency: int = Field(default=1, ge=1, le=4)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=45, gt=0, le=300)
    llm_json_schema_mode: Literal["auto", "required", "disabled"] = "auto"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)
