# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
web_root = project_root / "apps" / "web" / "out"

if not (web_root / "index.html").is_file():
    raise SystemExit("Run `pnpm build` before building the desktop distribution.")

analysis = Analysis(
    [str(project_root / "scripts" / "run_desktop.py")],
    pathex=[str(project_root), str(project_root / "packages" / "docalign_core")],
    binaries=[],
    datas=[
        (str(project_root / "alembic.ini"), "."),
        (str(project_root / "migrations"), "migrations"),
        (str(web_root), "apps/web/out"),
    ],
    hiddenimports=collect_submodules("uvicorn"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mypy", "pytest", "PyInstaller", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="DocAlign",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DocAlign",
)

if sys.platform == "darwin":
    app = BUNDLE(
        bundle,
        name="DocAlign.app",
        icon=None,
        bundle_identifier="app.docalign.desktop",
        version=APP_VERSION if "APP_VERSION" in globals() else "0.1.0",
    )
