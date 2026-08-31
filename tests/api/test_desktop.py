from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from apps.api import desktop


def test_default_data_dir_is_platform_appropriate(tmp_path: Path) -> None:
    assert desktop.default_data_dir(
        platform_name="darwin", environ={}, home=tmp_path
    ) == tmp_path / "Library" / "Application Support" / "DocAlign"
    assert desktop.default_data_dir(
        platform_name="win32", environ={"LOCALAPPDATA": "C:/Local"}, home=tmp_path
    ) == Path("C:/Local") / "DocAlign"
    assert desktop.default_data_dir(
        platform_name="linux", environ={"XDG_DATA_HOME": "/var/data"}, home=tmp_path
    ) == Path("/var/data/DocAlign")
    assert desktop.default_data_dir(
        platform_name="linux", environ={}, home=tmp_path
    ) == tmp_path / ".local" / "share" / "DocAlign"


def test_instance_lock_excludes_a_second_launcher(tmp_path: Path) -> None:
    first = desktop.InstanceLock(tmp_path / "instance.lock")
    second = desktop.InstanceLock(tmp_path / "instance.lock")

    assert first.acquire() is True
    assert second.acquire() is False
    second.release()
    first.release()
    assert second.acquire() is True
    second.release()


def test_port_selection_validates_range_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        desktop.select_port(0)

    real_socket = desktop.socket.socket
    occupied = real_socket(desktop.socket.AF_INET, desktop.socket.SOCK_STREAM)
    occupied.bind((desktop.HOST, 0))
    port = int(occupied.getsockname()[1])
    try:
        selected = desktop.select_port(port)
    finally:
        occupied.close()
    assert selected != port
    assert selected > 0


def test_activate_existing_opens_ready_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"port": 8765}', encoding="utf-8")
    browser = Mock(return_value=True)
    monkeypatch.setattr(desktop, "_health_ready", lambda port: port == 8765)
    monkeypatch.setattr(desktop.webbrowser, "open", browser)

    assert desktop.activate_existing(runtime, open_browser=True, wait_seconds=0.2)
    browser.assert_called_once_with("http://127.0.0.1:8765/")


def test_activate_existing_rejects_invalid_runtime_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"port": 99999}', encoding="utf-8")
    assert not desktop.activate_existing(runtime, open_browser=False, wait_seconds=0.01)


def test_self_test_and_cli_use_isolated_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    static_dir = tmp_path / "web"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("DocAlign", encoding="utf-8")

    report = desktop.run_self_test(static_dir)
    assert report["status"] == "ok"
    assert report["database_migrations"] is True
    assert desktop.main(["--self-test", "--static-dir", str(static_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["static_web"] is True


def test_self_test_rejects_missing_static_export(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="web application is incomplete"):
        desktop.run_self_test(tmp_path)


def test_packaged_launcher_exposes_offline_backup_verify_and_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "backup.zip"
    package.write_bytes(b"test")
    verification = Mock()
    verification.model_dump_json.return_value = '{"valid":true}'
    restored = Mock()
    restored.model_dump_json.return_value = '{"backup_id":"backup_test"}'
    verify = Mock(return_value=verification)
    restore = Mock(return_value=restored)
    monkeypatch.setattr(desktop, "verify_workspace_backup", verify)
    monkeypatch.setattr(desktop, "restore_workspace_backup", restore)

    assert desktop.main(["--verify-workspace-backup", str(package)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    verify.assert_called_once_with(package.resolve())

    target = tmp_path / "new-workspace"
    assert (
        desktop.main(
            [
                "--restore-workspace-backup",
                str(package),
                "--data-dir",
                str(target),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["backup_id"] == "backup_test"
    restore.assert_called_once_with(package.resolve(), target.resolve())

    with pytest.raises(SystemExit):
        desktop.main(["--restore-workspace-backup", str(package)])


def test_desktop_settings_ignore_launch_folder_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DOCALIGN_LLM_BASE_URL=https://unexpected.example/v1\n"
        "DOCALIGN_LLM_MODEL=unexpected\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOCALIGN_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("DOCALIGN_LLM_MODEL", raising=False)

    settings = desktop.desktop_settings(tmp_path / "workspace")

    assert settings.llm_configured is False
    assert settings.data_dir == tmp_path / "workspace"


def test_second_launch_activates_existing_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_dir = tmp_path / "web"
    static_dir.mkdir()
    data_dir = tmp_path / "data"
    existing = desktop.InstanceLock(data_dir / "instance.lock")
    assert existing.acquire()
    activate = Mock(return_value=True)
    monkeypatch.setattr(desktop, "activate_existing", activate)
    try:
        assert desktop.main(
            [
                "--data-dir",
                str(data_dir),
                "--static-dir",
                str(static_dir),
                "--no-browser",
            ]
        ) == 0
    finally:
        existing.release()
    activate.assert_called_once_with(data_dir / "runtime.json", open_browser=False)
