from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_distribution as distribution


def test_distribution_requires_static_web_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(distribution, "PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="Static web build is missing"):
        distribution.build_distribution(
            dist_dir=tmp_path / "dist",
            work_dir=tmp_path / "build",
            clean=True,
        )


def test_distribution_refuses_to_clean_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    web_root = tmp_path / "apps" / "web" / "out"
    web_root.mkdir(parents=True)
    (web_root / "index.html").write_text("DocAlign", encoding="utf-8")
    monkeypatch.setattr(distribution, "PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="Refusing to clean broad path"):
        distribution.build_distribution(
            dist_dir=tmp_path,
            work_dir=tmp_path / "build",
            clean=True,
        )
