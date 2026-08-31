from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

import uvicorn
from docalign_core.config import Settings
from docalign_core.workspace_backup import (
    WorkspaceBackupError,
    restore_workspace_backup,
    verify_workspace_backup,
)

from apps.api.db import Database
from apps.api.main import create_app
from apps.api.migrations import database_revisions, upgrade_database

APP_NAME = "DocAlign"
APP_VERSION = "0.1.0"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def default_data_dir(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return an OS-standard, per-user data directory independent of the launch folder."""

    current_platform = platform_name or sys.platform
    current_environ = environ if environ is not None else os.environ
    current_home = home or Path.home()
    if current_platform == "win32":
        local_app_data = current_environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else current_home / "AppData" / "Local"
        return base / APP_NAME
    if current_platform == "darwin":
        return current_home / "Library" / "Application Support" / APP_NAME
    xdg_data_home = current_environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else current_home / ".local" / "share"
    return base / APP_NAME


def bundled_web_root() -> Path:
    """Locate the static export in both source and PyInstaller one-folder layouts."""

    return Path(__file__).resolve().parents[2] / "apps" / "web" / "out"


class InstanceLock:
    """Small cross-platform advisory lock scoped to one DocAlign data directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":  # pragma: no cover - exercised in Windows CI
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            if sys.platform == "win32":  # pragma: no cover - exercised in Windows CI
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> Self:
        if not self.acquire():
            raise RuntimeError("DocAlign is already running for this data directory.")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def select_port(preferred: int = DEFAULT_PORT) -> int:
    if not 1 <= preferred <= 65_535:
        raise ValueError("Port must be between 1 and 65535.")
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, candidate))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    raise OSError("No local TCP port is available.")  # pragma: no cover


def _health_ready(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/v1/health", timeout=timeout
        ) as response:
            payload: object = json.loads(response.read())
            if response.status != 200 or not isinstance(payload, dict):
                return False
            return bool(payload.get("status") == "ok")
    except (OSError, ValueError, urllib.error.URLError):
        return False


def activate_existing(
    runtime_file: Path,
    *,
    open_browser: bool,
    wait_seconds: float = 3.0,
) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            payload = json.loads(runtime_file.read_text(encoding="utf-8"))
            port = int(payload["port"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if 1 <= port <= 65_535 and _health_ready(port):
            if open_browser:
                webbrowser.open(f"http://{HOST}:{port}/")
            return True
        time.sleep(0.1)
    return False


def _open_browser_when_ready(port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _health_ready(port):
            webbrowser.open(f"http://{HOST}:{port}/")
            return
        time.sleep(0.1)


def _write_runtime_file(path: Path, port: int) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "port": port, "version": APP_VERSION}),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_self_test(static_dir: Path) -> dict[str, object]:
    if not (static_dir / "index.html").is_file():
        raise RuntimeError("The bundled web application is incomplete.")
    with tempfile.TemporaryDirectory(prefix="docalign-self-test-") as directory:
        data_dir = Path(directory)
        database = Database(_sqlite_url(data_dir / "self-test.db"))
        try:
            upgrade_database(database)
            current, expected = database_revisions(database)
        finally:
            database.engine.dispose()
    if current != expected or not expected:
        raise RuntimeError("The bundled database migrations did not reach the expected revision.")
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "ok",
        "static_web": True,
        "database_migrations": True,
    }


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def desktop_settings(data_dir: Path) -> Settings:
    """Load explicit process environment only; never trust a launch-folder .env file."""

    return Settings(
        _env_file=None,
        data_dir=data_dir,
        database_url=_sqlite_url(data_dir / "docalign.db"),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the local DocAlign application.")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--static-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    backup_group = parser.add_mutually_exclusive_group()
    backup_group.add_argument("--verify-workspace-backup", type=Path, default=None)
    backup_group.add_argument("--restore-workspace-backup", type=Path, default=None)
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.restore_workspace_backup is not None and arguments.data_dir is None:
        parser.error("--restore-workspace-backup requires an explicit --data-dir.")
    if arguments.verify_workspace_backup is not None:
        try:
            verification = verify_workspace_backup(
                arguments.verify_workspace_backup.expanduser().resolve()
            )
        except WorkspaceBackupError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 2
        print(verification.model_dump_json(indent=2))
        return 0
    if arguments.restore_workspace_backup is not None:
        try:
            receipt = restore_workspace_backup(
                arguments.restore_workspace_backup.expanduser().resolve(),
                arguments.data_dir.expanduser().resolve(),
            )
        except WorkspaceBackupError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 2
        print(receipt.model_dump_json(indent=2))
        return 0
    data_dir = (arguments.data_dir or default_data_dir()).expanduser().resolve()
    static_dir = (arguments.static_dir or bundled_web_root()).expanduser().resolve()

    if arguments.self_test:
        print(json.dumps(run_self_test(static_dir), ensure_ascii=False, sort_keys=True))
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_file = data_dir / "runtime.json"
    lock = InstanceLock(data_dir / "instance.lock")
    if not lock.acquire():
        if activate_existing(runtime_file, open_browser=not arguments.no_browser):
            return 0
        raise RuntimeError(
            "Another DocAlign process holds the workspace lock but is not responding."
        )

    try:
        port = select_port(arguments.port)
        settings = desktop_settings(data_dir)
        server_holder: list[uvicorn.Server] = []

        def request_shutdown() -> None:
            if server_holder:
                server_holder[0].should_exit = True

        application = create_app(
            settings,
            static_dir=static_dir,
            desktop_shutdown=request_shutdown,
        )
        _write_runtime_file(runtime_file, port)
        if not arguments.no_browser:
            threading.Thread(
                target=_open_browser_when_ready,
                args=(port,),
                daemon=True,
                name="docalign-browser-opener",
            ).start()
        config = uvicorn.Config(
            application,
            host=HOST,
            port=port,
            access_log=False,
            log_level="info",
        )
        server = uvicorn.Server(config)
        server_holder.append(server)
        server.run()
        return 0
    finally:
        runtime_file.unlink(missing_ok=True)
        lock.release()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
