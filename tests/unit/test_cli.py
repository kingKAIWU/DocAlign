from __future__ import annotations

import json
from pathlib import Path

from docalign_core import cli
from docalign_core.domain.formatting_spec import default_academic_spec
from docalign_core.llm.base import RequirementCompilationResult
from typer.testing import CliRunner


def test_cli_analyze_format_and_validate(academic_docx: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    analysis_path = tmp_path / "analysis.json"
    analyzed = runner.invoke(
        cli.app,
        ["analyze", str(academic_docx), "--out", str(analysis_path)],
    )
    assert analyzed.exit_code == 0, analyzed.output
    assert analysis_path.exists()

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(default_academic_spec().model_dump_json(), encoding="utf-8")
    output_path = tmp_path / "formatted.docx"
    blocked = runner.invoke(
        cli.app,
        [
            "format",
            str(academic_docx),
            "--spec",
            str(spec_path),
            "--out",
            str(output_path),
        ],
    )
    assert blocked.exit_code == 2
    assert "PROCESSING_BOUNDARY_ACKNOWLEDGMENT_REQUIRED" in blocked.output
    assert not output_path.exists()

    formatted = runner.invoke(
        cli.app,
        [
            "format",
            str(academic_docx),
            "--spec",
            str(spec_path),
            "--out",
            str(output_path),
            "--acknowledge-processing-boundary",
        ],
    )
    assert formatted.exit_code == 0, formatted.output
    assert output_path.exists()
    audit_path = tmp_path / "formatted.docalign" / "audit.json"
    assert audit_path.exists()
    acknowledgment = json.loads(audit_path.read_text(encoding="utf-8"))[
        "source_processing_boundary_acknowledgment"
    ]
    assert acknowledgment["method"] == "explicit_cli"
    assert acknowledgment["acknowledged"] is True
    assert acknowledgment["acknowledged_at"] is not None

    report_path = tmp_path / "validation.json"
    validated = runner.invoke(
        cli.app,
        [
            "validate",
            str(output_path),
            "--spec",
            str(spec_path),
            "--report",
            str(report_path),
        ],
    )
    assert validated.exit_code == 0, validated.output
    assert report_path.exists()


def test_cli_rejects_source_overwrite(academic_docx: Path, tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(default_academic_spec().model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "format",
            str(academic_docx),
            "--spec",
            str(spec_path),
            "--out",
            str(academic_docx),
        ],
    )
    assert result.exit_code != 0
    assert "must not overwrite" in result.output


def test_cli_spec_compile_uses_structured_result(monkeypatch: object, tmp_path: Path) -> None:
    class FakeInterpreter:
        def __init__(self, **_: object) -> None:
            pass

        async def compile_requirements(
            self, instruction: str, document_summary: object
        ) -> RequirementCompilationResult:
            assert instruction == "正文宋体小四"
            assert document_summary is not None
            return RequirementCompilationResult(
                spec=default_academic_spec(),
                provider="mock",
                model="mock-model",
            )

    monkeypatch.setattr(cli, "OpenAICompatibleChatInterpreter", FakeInterpreter)
    monkeypatch.setenv("DOCALIGN_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("DOCALIGN_LLM_MODEL", "mock-model")
    target = tmp_path / "compiled.json"
    result = CliRunner().invoke(
        cli.app,
        ["spec", "compile", "--instruction", "正文宋体小四", "--out", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert target.exists()


def test_cli_import_text_creates_docx(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("# 标题\n正文内容\n- 项目一", encoding="utf-8")
    target = tmp_path / "imported.docx"

    result = CliRunner().invoke(
        cli.app, ["import-text", str(source), "--out", str(target)]
    )

    assert result.exit_code == 0, result.output
    assert target.read_bytes().startswith(b"PK")
