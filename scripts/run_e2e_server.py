from __future__ import annotations

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from docalign_core.config import Settings

from apps.api.main import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an isolated production-style DocAlign server for browser tests."
    )
    parser.add_argument("--port", type=int, default=18_080)
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    static_dir = project_root / "apps" / "web" / "out"
    if not (static_dir / "index.html").is_file():
        raise SystemExit("Static web build is missing; run `pnpm build` before `pnpm e2e`.")

    with tempfile.TemporaryDirectory(prefix="docalign-e2e-") as directory:
        data_dir = Path(directory) / "data"
        settings = Settings(
            _env_file=None,
            data_dir=data_dir,
            database_url=f"sqlite:///{data_dir / 'state.db'}",
        )
        application = create_app(settings, static_dir=static_dir)
        uvicorn.run(
            application,
            host="127.0.0.1",
            port=arguments.port,
            access_log=False,
            log_level="info",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
