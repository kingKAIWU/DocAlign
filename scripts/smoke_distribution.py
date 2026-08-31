from __future__ import annotations

import argparse
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def _wait_for_page(url: str, expected: bytes, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    last_error = "not started"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"DocAlign exited before becoming ready (code {process.returncode})."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                body = response.read()
                if response.status == 200 and expected in body:
                    return
                last_error = f"unexpected response {response.status}"
        except (OSError, urllib.error.URLError) as caught:
            last_error = type(caught).__name__
        time.sleep(0.1)
    raise RuntimeError(f"DocAlign did not serve {url}: {last_error}")


def _request_graceful_shutdown(port: int) -> None:
    origin = f"http://{HOST}:{port}"
    request = urllib.request.Request(
        f"{origin}/api/v1/system/quit",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-DocAlign-Action": "quit",
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 202 or b"shutting_down" not in response.read():
            raise RuntimeError("The distribution rejected its graceful shutdown request.")


def _download_workspace_backup(port: int, target: Path) -> None:
    with urllib.request.urlopen(
        f"http://{HOST}:{port}/api/v1/workspace/backup", timeout=20
    ) as response:
        content_type = response.headers.get_content_type()
        payload = response.read()
    if content_type != "application/zip" or not payload.startswith(b"PK"):
        raise RuntimeError("The packaged application did not return a workspace backup ZIP.")
    target.write_bytes(payload)


def smoke_distribution(launcher: Path) -> None:
    if not launcher.is_file():
        raise RuntimeError(f"Distribution launcher does not exist: {launcher}")

    with tempfile.TemporaryDirectory(prefix="docalign-distribution-smoke-") as directory:
        test_root = Path(directory)
        launch_dir = test_root / "launch-directory"
        data_dir = test_root / "workspace"
        launch_dir.mkdir()
        subprocess.run([launcher, "--self-test"], check=True, timeout=30, cwd=launch_dir)
        port = _free_port()
        command = [
            launcher,
            "--no-browser",
            "--data-dir",
            data_dir,
            "--port",
            str(port),
        ]
        process = subprocess.Popen(command, cwd=launch_dir)
        try:
            _wait_for_page(
                f"http://{HOST}:{port}/api/v1/health",
                b'"status":"ok"',
                process,
            )
            _wait_for_page(f"http://{HOST}:{port}/", b"DocAlign", process)
            _wait_for_page(f"http://{HOST}:{port}/settings/", b"DocAlign", process)
            second = subprocess.run(command, check=False, timeout=10, cwd=launch_dir)
            if second.returncode != 0:
                raise RuntimeError("A second launch did not activate the existing instance.")
            if (launch_dir / "data").exists():
                raise RuntimeError("The distribution wrote data into its launch directory.")
            backup = test_root / "workspace-backup.zip"
            _download_workspace_backup(port, backup)
            subprocess.run(
                [launcher, "--verify-workspace-backup", backup],
                check=True,
                timeout=30,
                cwd=launch_dir,
            )
            restored_dir = test_root / "restored-workspace"
            subprocess.run(
                [
                    launcher,
                    "--restore-workspace-backup",
                    backup,
                    "--data-dir",
                    restored_dir,
                ],
                check=True,
                timeout=30,
                cwd=launch_dir,
            )
            if not (restored_dir / "docalign.db").is_file():
                raise RuntimeError("The packaged restore command did not create its database.")
            _request_graceful_shutdown(port)
            process.wait(timeout=20)
            if process.returncode != 0:
                raise RuntimeError(
                    f"DocAlign returned {process.returncode} after graceful shutdown."
                )
            if (data_dir / "runtime.json").exists():
                raise RuntimeError("Graceful shutdown left stale runtime metadata behind.")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
                    process.kill()
                    process.wait(timeout=5)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test a built DocAlign application.")
    parser.add_argument("--launcher", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    smoke_distribution(arguments.launcher.resolve())
    print(f"Distribution smoke test passed: {arguments.launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
