from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

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


def test_desktop_shutdown_requires_explicit_same_origin_action(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )
    shutdown = Mock()

    with TestClient(create_app(settings, desktop_shutdown=shutdown)) as client:
        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["desktop_app"] is True

        missing_action = client.post("/api/v1/system/quit", json={})
        assert missing_action.status_code == 403
        assert missing_action.json()["error"]["code"] == "DESKTOP_ACTION_FORBIDDEN"

        cross_site = client.post(
            "/api/v1/system/quit",
            json={},
            headers={
                "X-DocAlign-Action": "quit",
                "Sec-Fetch-Site": "cross-site",
                "Origin": "https://example.invalid",
            },
        )
        assert cross_site.status_code == 403

        mismatched_origin = client.post(
            "/api/v1/system/quit",
            json={},
            headers={
                "X-DocAlign-Action": "quit",
                "Sec-Fetch-Site": "same-origin",
                "Origin": "https://example.invalid",
            },
        )
        assert mismatched_origin.status_code == 403

        accepted = client.post(
            "/api/v1/system/quit",
            json={},
            headers={
                "X-DocAlign-Action": "quit",
                "Sec-Fetch-Site": "same-origin",
                "Origin": "http://testserver",
            },
        )
        assert accepted.status_code == 202
        assert accepted.json() == {"status": "shutting_down"}
        shutdown.assert_called_once_with()


def test_development_runtime_does_not_expose_desktop_shutdown(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'state.db'}",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/capabilities").json()["desktop_app"] is False
        response = client.post(
            "/api/v1/system/quit",
            json={},
            headers={"X-DocAlign-Action": "quit"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "DESKTOP_ACTION_UNAVAILABLE"
