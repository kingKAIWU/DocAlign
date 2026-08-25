from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from docalign_core.config import Settings

from apps.api.main import create_app


def rendered_openapi() -> str:
    with tempfile.TemporaryDirectory(prefix="docalign-openapi-") as directory:
        data_dir = Path(directory)
        application = create_app(
            Settings(
                data_dir=data_dir,
                database_url=f"sqlite:///{data_dir / 'schema.db'}",
            )
        )
        return json.dumps(application.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path("schemas/openapi.v1.json")
    content = rendered_openapi()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI schema drift detected; run `make schemas`.")
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
