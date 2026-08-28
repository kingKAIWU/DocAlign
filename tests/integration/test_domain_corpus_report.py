from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from shutil import copy2
from typing import cast


def test_report_distinguishes_safe_layout_changes_from_exact_fingerprint(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    runner_namespace = runpy.run_path(
        str(project_root / "tests/fixtures/domain-corpus/run_corpus.py"),
        run_name="domain_corpus_runner",
    )
    run_corpus = cast(
        Callable[[Path, Path], list[dict[str, object]]],
        runner_namespace["run"],
    )
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    copy2(
        project_root / "tests/fixtures/domain-corpus/source/02-academic-paper.docx",
        source_dir / "02-academic-paper.docx",
    )

    reports = run_corpus(source_dir, tmp_path / "output")

    assert len(reports) == 1
    assert reports[0]["content_preserved"] is True
    assert reports[0]["exact_fingerprint_preserved"] is False
    assert reports[0]["reviewable_unknowns"] == 2
    assert reports[0]["auto_layout_splits"] == 1
    assert reports[0]["validation_valid"] is True
