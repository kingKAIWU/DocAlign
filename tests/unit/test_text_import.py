from __future__ import annotations

from pathlib import Path

import pytest
from docalign_core.analysis.classifier import analyze_document
from docalign_core.docx.parser import parse_docx
from docalign_core.docx.text_import import PlainTextImportError, create_docx_from_text
from docalign_core.domain.document_ir import ParagraphIR
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import default_academic_spec
from docalign_core.services.processing import process_document


def test_plain_text_import_builds_real_headings_and_lists(tmp_path: Path) -> None:
    source = tmp_path / "plain.docx"
    create_docx_from_text(
        """# 智能排版研究

## 背景
这是正文 English body。
- 保留内容
- 自动识别结构
1. 第一步
2. 第二步
""",
        source,
    )

    analysis = analyze_document(parse_docx(source))
    paragraphs = [
        block
        for block in analysis.document_ir.blocks
        if isinstance(block, ParagraphIR)
    ]
    assert [paragraph.text for paragraph in paragraphs] == [
        "智能排版研究",
        "背景",
        "这是正文 English body。",
        "保留内容",
        "自动识别结构",
        "第一步",
        "第二步",
    ]
    assert paragraphs[0].detected_role == SemanticRole.TITLE
    assert paragraphs[1].detected_role == SemanticRole.HEADING_1
    assert all(paragraph.numbering is not None for paragraph in paragraphs[3:])
    assert all(
        paragraph.detected_role == SemanticRole.LIST_ITEM for paragraph in paragraphs[3:]
    )

    output = tmp_path / "formatted.docx"
    result = process_document(
        source,
        analysis.document_ir,
        default_academic_spec(),
        output,
        job_id="job-text-import",
        artifact_dir=tmp_path / "text-import-artifacts",
    )
    assert result.audit.validation.valid
    assert parse_docx(output).content_fingerprint.digest == (
        analysis.document_ir.content_fingerprint.digest
    )


def test_plain_text_import_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(PlainTextImportError) as captured:
        create_docx_from_text("  \n\n", tmp_path / "empty.docx")
    assert captured.value.code == "TEXT_EMPTY"
