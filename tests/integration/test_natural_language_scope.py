from __future__ import annotations

from pathlib import Path

import pytest
from docalign_core.analysis.classifier import analyze_document
from docalign_core.docx.parser import parse_docx
from docalign_core.domain.document_ir import ParagraphIR
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    FontSpec,
    FormattingSpec,
    LineSpacingMode,
    LineSpacingSpec,
    ParagraphSpec,
    RoleFormattingSpec,
)
from docalign_core.services.processing import process_document

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "natural-language-scope"
FIXTURES = [
    "mixed-body.docx",
    "numbered-lists.docx",
    "long-numbered-sentence.docx",
    "sections-and-tables.docx",
]


def _body_spec() -> FormattingSpec:
    return FormattingSpec(
        roles={
            SemanticRole.BODY: RoleFormattingSpec(
                font=FontSpec(
                    east_asia="宋体",
                    ascii="Times New Roman",
                    high_ansi="Times New Roman",
                    complex_script="Times New Roman",
                    size_pt=12,
                ),
                paragraph=ParagraphSpec(
                    line_spacing=LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=1.5),
                    first_line_indent_pt=24,
                ),
            )
        }
    )


@pytest.mark.parametrize("filename", FIXTURES)
def test_scoped_natural_language_formatting_is_safe_and_idempotent(
    filename: str, tmp_path: Path
) -> None:
    source = FIXTURE_DIR / filename
    source_ir = parse_docx(source)
    analysis = analyze_document(source_ir)
    spec = _body_spec()
    first_output = tmp_path / f"first-{filename}"
    first = process_document(
        source,
        analysis.document_ir,
        spec,
        first_output,
        job_id=f"first-{source.stem}",
        artifact_dir=tmp_path / f"first-{source.stem}",
    )
    assert first.audit.validation.valid
    first_ir = parse_docx(first_output)
    assert first_ir.content_fingerprint.digest == source_ir.content_fingerprint.digest
    assert first_ir.sections == source_ir.sections
    assert not any(
        operation.operation_type.value == "set_section_layout"
        for operation in first.plan.operations
    )

    expected_by_index = {block.index: block for block in analysis.document_ir.blocks}
    for block in first_ir.blocks:
        expected = expected_by_index[block.index]
        if not isinstance(block, ParagraphIR) or not isinstance(expected, ParagraphIR):
            continue
        if expected.numbering is not None:
            assert block.numbering == expected.numbering
            assert block.current_style_name == expected.current_style_name
        elif expected.detected_role != SemanticRole.BODY:
            assert block.current_style_name == expected.current_style_name

    second_analysis = analyze_document(first_ir)
    second_output = tmp_path / f"second-{filename}"
    second = process_document(
        first_output,
        second_analysis.document_ir,
        spec,
        second_output,
        job_id=f"second-{source.stem}",
        artifact_dir=tmp_path / f"second-{source.stem}",
    )
    assert second.audit.validation.valid
    assert second.audit.summary.changed_mutations == 0
    assert (
        parse_docx(second_output).content_fingerprint.digest
        == source_ir.content_fingerprint.digest
    )


def test_long_numbered_sentence_fixture_has_expected_role_boundary() -> None:
    analysis = analyze_document(parse_docx(FIXTURE_DIR / "long-numbered-sentence.docx"))
    roles = {
        block.text: block.detected_role
        for block in analysis.document_ir.blocks
        if isinstance(block, ParagraphIR)
    }
    long_sentence = next(text for text in roles if text.startswith("1.1 现状分析 当前 Word"))
    assert roles[long_sentence] == SemanticRole.BODY
    assert roles["1.2 真正的二级标题"] == SemanticRole.HEADING_2
