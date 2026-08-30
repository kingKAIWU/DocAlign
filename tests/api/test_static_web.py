from __future__ import annotations

from pathlib import Path

import pytest
from docalign_core.config import Settings
from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_packaged_app_serves_web_without_shadowing_api(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    settings_dir = web_root / "settings"
    settings_dir.mkdir(parents=True)
    (web_root / "index.html").write_text("<h1>DocAlign</h1>", encoding="utf-8")
    (settings_dir / "index.html").write_text("<h1>Settings</h1>", encoding="utf-8")

    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )
    with TestClient(create_app(settings, static_dir=web_root)) as client:
        assert client.get("/").text == "<h1>DocAlign</h1>"
        assert client.get("/settings/").text == "<h1>Settings</h1>"
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/docs").status_code == 200


def test_packaged_app_rejects_incomplete_web_build(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )

    missing_index = tmp_path / "web"
    missing_index.mkdir()

    with pytest.raises(ValueError, match="index.html"):
        create_app(settings, static_dir=missing_index)
