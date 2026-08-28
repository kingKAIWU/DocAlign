from __future__ import annotations

import pytest
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    FontSpec,
    FormattingProperty,
    FormattingSpec,
    LineSpacingMode,
    LineSpacingSpec,
    ParagraphSpec,
    RoleFormattingSpec,
    VisualCleanupSpec,
    cleanup_preset_catalog,
    compact_cleanup_spec,
    contract_cleanup_spec,
    default_academic_spec,
    default_cleanup_spec,
    merge_specs,
    normalize_font_size,
    resolve_role_spec,
    wide_table_cleanup_spec,
)


def test_chinese_font_size_normalization() -> None:
    assert normalize_font_size("小四") == 12
    assert normalize_font_size("10.5pt") == 10.5
    assert normalize_font_size(16) == 16


def test_exact_line_spacing_requires_value() -> None:
    with pytest.raises(ValueError):
        LineSpacingSpec(mode=LineSpacingMode.EXACT)


def test_default_spec_schema_round_trip() -> None:
    spec = default_academic_spec()
    assert FormattingSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.schema_version == "formatting-spec.v1"
    assert spec.roles[SemanticRole.TITLE].font is not None
    assert spec.roles[SemanticRole.TITLE].font.size_pt == 22
    assert spec.roles[SemanticRole.AUTHOR_INFO].paragraph is not None
    assert spec.roles[SemanticRole.ABSTRACT_HEADING].paragraph is not None
    assert spec.roles[SemanticRole.KEYWORDS].paragraph is not None
    assert spec.roles[SemanticRole.BIBLIOGRAPHY_HEADING].font is not None
    assert spec.roles[SemanticRole.APPENDIX_HEADING].font is not None


def test_neutral_spec_preserves_page_layout_until_explicitly_requested() -> None:
    assert FormattingSpec().document is None


def test_default_cleanup_spec_is_deterministic_and_forceful() -> None:
    spec = default_cleanup_spec()
    assert spec.source.preset_id == "default-clean-cn"
    assert spec.document is not None
    assert spec.document.page.force_orientation_all_sections
    assert not spec.document.page.preserve_existing_landscape_sections
    assert spec.visual_cleanup is not None
    assert spec.visual_cleanup.text_color_hex == "000000"
    assert spec.visual_cleanup.remove_text_highlight
    assert spec.visual_cleanup.remove_character_shading
    assert spec.visual_cleanup.remove_paragraph_shading
    assert spec.visual_cleanup.remove_table_cell_shading
    assert spec.visual_cleanup.remove_page_background
    assert not spec.behavior.preserve_inline_emphasis
    assert spec.behavior.apply_to_unknown_roles
    assert spec.tables is not None
    assert spec.tables.grid_borders
    assert spec.tables.repeat_header_row
    assert spec.tables.prevent_row_split
    assert spec.tables.adaptive_column_widths
    assert spec.tables.adaptive_font_size
    assert all(
        role.paragraph is None
        or (
            role.paragraph.keep_with_next is False
            and role.paragraph.keep_lines_together is False
            and role.paragraph.page_break_before is False
            and role.paragraph.widow_orphan_control is False
        )
        for role in spec.roles.values()
    )

    body = resolve_role_spec(spec, SemanticRole.BODY)
    title = resolve_role_spec(spec, SemanticRole.TITLE)
    assert body is not None and body.font is not None
    assert body.font.east_asia == "宋体"
    assert body.font.size_pt == 12
    assert body.font.bold is False
    assert title is not None and title.font is not None
    assert title.font.east_asia == "宋体"
    assert title.font.size_pt == 22
    assert title.font.bold is True


def test_cleanup_catalog_covers_compact_contract_and_wide_table_scenarios() -> None:
    catalog = cleanup_preset_catalog()
    assert [item.preset_id for item in catalog] == [
        "default-clean-cn",
        "compact-clean-cn",
        "contract-clean-cn",
        "wide-table-clean-cn",
    ]
    assert all(item.metadata.claim_level == "generic" for item in catalog)
    assert all(item.metadata.pack_version == "1.0.0" for item in catalog)
    assert all(item.metadata.covered_capabilities for item in catalog)
    assert all(
        any("不代表" in limitation for limitation in item.metadata.limitations)
        for item in catalog
    )
    compact = compact_cleanup_spec()
    assert compact.roles[SemanticRole.BODY].font.size_pt == 11
    assert compact.roles[SemanticRole.HEADING_1].paragraph.alignment == "left"
    contract = contract_cleanup_spec()
    assert contract.roles[SemanticRole.HEADING_2].paragraph.alignment == "left"
    wide = wide_table_cleanup_spec()
    assert wide.document is not None
    assert wide.document.page.preserve_existing_landscape_sections
    assert not wide.document.page.force_orientation_all_sections


def test_visual_cleanup_color_is_normalized() -> None:
    cleanup = VisualCleanupSpec(text_color_hex="#000000")
    assert cleanup.text_color_hex == "000000"


def test_spec_merge_keeps_preset_defaults_and_applies_later_override() -> None:
    preset = default_academic_spec()
    override = FormattingSpec(
        roles={SemanticRole.BODY: RoleFormattingSpec(font=FontSpec(size_pt=10.5))}
    )
    merged = merge_specs(preset, override)
    assert merged.roles[SemanticRole.BODY].font is not None
    assert merged.roles[SemanticRole.BODY].font.size_pt == 10.5
    assert merged.roles[SemanticRole.BODY].font.east_asia == "宋体"
    assert FormattingProperty.FONT_EAST_ASIA in merged.roles[SemanticRole.BODY].force.properties
    assert SemanticRole.HEADING_1 in merged.roles


def test_document_baseline_is_inherited_and_role_override_wins() -> None:
    spec = FormattingSpec(
        baseline=RoleFormattingSpec(
            font=FontSpec(east_asia="宋体", ascii="Times New Roman", size_pt=12)
        ),
        roles={
            SemanticRole.HEADING_1: RoleFormattingSpec(
                font=FontSpec(east_asia="黑体", size_pt=16)
            )
        },
    )

    body = resolve_role_spec(spec, SemanticRole.BODY)
    heading = resolve_role_spec(spec, SemanticRole.HEADING_1)
    assert body is not None and body.font is not None
    assert body.font.east_asia == "宋体"
    assert body.font.ascii == "Times New Roman"
    assert heading is not None and heading.font is not None
    assert heading.font.east_asia == "黑体"
    assert heading.font.ascii == "Times New Roman"
    assert heading.font.size_pt == 16


def test_hanging_indent_role_replaces_baseline_first_line_indent() -> None:
    spec = FormattingSpec(
        baseline=RoleFormattingSpec(
            paragraph=ParagraphSpec(first_line_indent_pt=24)
        ),
        roles={
            SemanticRole.BIBLIOGRAPHY_ENTRY: RoleFormattingSpec(
                paragraph=ParagraphSpec(hanging_indent_pt=21)
            )
        },
    )

    bibliography = resolve_role_spec(spec, SemanticRole.BIBLIOGRAPHY_ENTRY)
    assert bibliography is not None and bibliography.paragraph is not None
    assert bibliography.paragraph.hanging_indent_pt == 21
    assert bibliography.paragraph.first_line_indent_pt is None


def test_first_line_indent_role_replaces_baseline_hanging_indent() -> None:
    spec = FormattingSpec(
        baseline=RoleFormattingSpec(paragraph=ParagraphSpec(hanging_indent_pt=21)),
        roles={
            SemanticRole.BODY: RoleFormattingSpec(
                paragraph=ParagraphSpec(first_line_indent_pt=24)
            )
        },
    )

    body = resolve_role_spec(spec, SemanticRole.BODY)
    assert body is not None and body.paragraph is not None
    assert body.paragraph.first_line_indent_pt == 24
    assert body.paragraph.hanging_indent_pt is None


def test_later_role_indent_replaces_mutually_exclusive_preset_indent() -> None:
    override = FormattingSpec(
        roles={
            SemanticRole.BIBLIOGRAPHY_ENTRY: RoleFormattingSpec(
                paragraph=ParagraphSpec(first_line_indent_pt=0)
            )
        }
    )

    merged = merge_specs(default_academic_spec(), override)
    paragraph = merged.roles[SemanticRole.BIBLIOGRAPHY_ENTRY].paragraph
    assert paragraph is not None
    assert paragraph.first_line_indent_pt == 0
    assert paragraph.hanging_indent_pt is None
