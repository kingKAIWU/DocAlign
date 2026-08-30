from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_distribution(
    *,
    dist_dir: Path,
    work_dir: Path,
    clean: bool,
) -> Path:
    web_index = PROJECT_ROOT / "apps" / "web" / "out" / "index.html"
    if not web_index.is_file():
        raise RuntimeError("Static web build is missing. Run `pnpm build` first.")

    if clean:
        for target in (dist_dir, work_dir):
            resolved = target.resolve()
            if resolved in {PROJECT_ROOT, PROJECT_ROOT.parent, Path(resolved.anchor)}:
                raise RuntimeError(f"Refusing to clean broad path: {resolved}")
            shutil.rmtree(resolved, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        str(PROJECT_ROOT / "packaging" / "DocAlign.spec"),
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    expected = (
        dist_dir / "DocAlign.app" / "Contents" / "MacOS" / "DocAlign"
        if sys.platform == "darwin"
        else dist_dir / "DocAlign" / ("DocAlign.exe" if sys.platform == "win32" else "DocAlign")
    )
    if not expected.is_file():
        raise RuntimeError(f"Distribution build did not create the expected launcher: {expected}")
    return expected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a native DocAlign distribution.")
    parser.add_argument("--dist-dir", type=Path, default=PROJECT_ROOT / "dist" / "distribution")
    parser.add_argument("--work-dir", type=Path, default=PROJECT_ROOT / "build" / "pyinstaller")
    parser.add_argument("--no-clean", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    launcher = build_distribution(
        dist_dir=arguments.dist_dir.resolve(),
        work_dir=arguments.work_dir.resolve(),
        clean=not arguments.no_clean,
    )
    print(f"Built DocAlign for {platform.system()}: {launcher}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
