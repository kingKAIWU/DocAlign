from __future__ import annotations

from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    Alignment,
    FormattingProperty,
    PageSize,
    default_academic_spec,
)
from docalign_core.llm.scope import applied_capabilities, scope_natural_language_spec


def test_unscoped_typography_request_cannot_expand_into_preset_layout() -> None:
    model_spec = default_academic_spec()

    scoped = scope_natural_language_spec(
        "中文宋体小四，英文 Times New Roman，首行缩进两字符，1.5 倍行距。",
        model_spec,
    )

    assert scoped.document is None
    assert set(scoped.roles) == {SemanticRole.BODY}
    assert scoped.tables is None
    assert scoped.figures is None
    body = scoped.roles[SemanticRole.BODY]
    assert body.font is not None
    assert body.font.east_asia == "宋体"
    assert body.font.ascii == "Times New Roman"
    assert body.font.high_ansi == "Times New Roman"
    assert body.font.complex_script is None
    assert body.font.size_pt == 12
    assert body.paragraph is not None
    assert body.paragraph.first_line_indent_pt == 24
    assert body.paragraph.line_spacing is not None
    assert body.paragraph.line_spacing.value == 1.5
    assert body.paragraph.alignment is None
    assert body.paragraph.space_before_pt is None
    assert body.paragraph.space_after_pt is None
    assert body.force.properties == {
        FormattingProperty.FONT_EAST_ASIA,
        FormattingProperty.FONT_ASCII,
        FormattingProperty.FONT_HIGH_ANSI,
        FormattingProperty.FONT_SIZE,
        FormattingProperty.PARAGRAPH_FIRST_LINE_INDENT,
        FormattingProperty.PARAGRAPH_LINE_SPACING,
    }


def test_explicit_page_and_heading_request_keeps_only_named_properties() -> None:
    scoped = scope_natural_language_spec(
        "A4 纸张，一级标题黑体三号居中。",
        default_academic_spec(),
    )

    assert scoped.document is not None
    page = scoped.document.page
    assert page.size == PageSize.A4
    assert page.orientation is None
    assert page.margin_top_mm is None
    assert page.margin_bottom_mm is None
    assert page.margin_left_mm is None
    assert page.margin_right_mm is None
    assert set(scoped.roles) == {SemanticRole.HEADING_1}
    heading = scoped.roles[SemanticRole.HEADING_1]
    assert heading.font is not None
    assert heading.font.east_asia == "黑体"
    assert heading.font.ascii is None
    assert heading.font.size_pt == 16
    assert heading.paragraph is not None
    assert heading.paragraph.alignment == Alignment.CENTER
    assert heading.paragraph.space_before_pt is None
    assert heading.paragraph.space_after_pt is None


def test_model_assumptions_for_discarded_scope_are_removed() -> None:
    model_spec = default_academic_spec()
    model_spec.source.assumptions = [
        "小四 corresponds to 12pt.",
        "Headings use the same font for consistency.",
        "Margins not specified; defaults used.",
        "Line spacing also applies to unknown roles.",
    ]

    scoped = scope_natural_language_spec("正文宋体小四，1.5 倍行距。", model_spec)

    assert "小四 corresponds to 12pt." in scoped.source.assumptions
    assert all("Heading" not in item for item in scoped.source.assumptions)
    assert all("Margin" not in item for item in scoped.source.assumptions)
    assert all("unknown" not in item for item in scoped.source.assumptions)
    assert scoped.source.assumptions[-1].startswith("本地作用域控制")


def test_whole_document_request_compiles_to_baseline_not_role_duplication() -> None:
    scoped = scope_natural_language_spec(
        "全文中文宋体小四，英文 Times New Roman，1.5 倍行距。",
        default_academic_spec(),
    )

    assert scoped.baseline is not None
    assert scoped.roles == {}
    assert scoped.baseline.font is not None
    assert scoped.baseline.font.east_asia == "宋体"
    assert scoped.baseline.font.ascii == "Times New Roman"
    assert scoped.baseline.paragraph is not None
    assert scoped.baseline.paragraph.line_spacing is not None
    assert scoped.baseline.paragraph.line_spacing.value == 1.5


def test_all_text_black_and_no_background_compile_to_visual_cleanup() -> None:
    scoped = scope_natural_language_spec(
        "所有颜色改为黑色且不需要背景。",
        default_academic_spec(),
    )

    cleanup = scoped.visual_cleanup
    assert cleanup is not None
    assert cleanup.text_color_hex == "000000"
    assert cleanup.remove_text_highlight
    assert cleanup.remove_character_shading
    assert cleanup.remove_paragraph_shading
    assert cleanup.remove_table_cell_shading
    assert cleanup.remove_page_background
    assert any("图片、形状、边框和线条保持原样" in item for item in scoped.source.assumptions)
    assert any("清除文本高亮" in item for item in scoped.source.assumptions)
    assert applied_capabilities(scoped) == [
        "document_text_color",
        "document_background_cleanup",
    ]


def test_explicit_auto_layout_request_enables_local_structural_capability() -> None:
    scoped = scope_natural_language_spec(
        "请自动排版，识别各级标题并对正文分段。",
        default_academic_spec(),
    )

    assert scoped.auto_layout.enabled
    assert "auto_layout" in applied_capabilities(scoped)
    assert any("受保护结构保持原样" in item for item in scoped.source.assumptions)
