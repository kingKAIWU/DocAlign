from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from docalign_core.domain.base import StrictModel
from docalign_core.domain.enums import SemanticRole

CHINESE_FONT_SIZES_PT: dict[str, float] = {
    "初号": 42,
    "小初": 36,
    "一号": 26,
    "小一": 24,
    "二号": 22,
    "小二": 18,
    "三号": 16,
    "小三": 15,
    "四号": 14,
    "小四": 12,
    "五号": 10.5,
    "小五": 9,
    "六号": 7.5,
    "小六": 6.5,
    "七号": 5.5,
    "八号": 5,
}


class Alignment(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class LineSpacingMode(StrEnum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    EXACT = "exact"
    AT_LEAST = "at_least"


class PageSize(StrEnum):
    A4 = "A4"
    LETTER = "LETTER"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class TableWidthPolicy(StrEnum):
    PRESERVE = "preserve"
    FIT_PRINTABLE_WIDTH = "fit_printable_width"
    FIXED = "fixed"


class SpecSourceType(StrEnum):
    SYSTEM = "system"
    PRESET = "preset"
    NATURAL_LANGUAGE = "natural_language"
    STRUCTURED = "structured"
    MERGED = "merged"


class FormattingProperty(StrEnum):
    FONT_EAST_ASIA = "font.east_asia"
    FONT_ASCII = "font.ascii"
    FONT_HIGH_ANSI = "font.high_ansi"
    FONT_COMPLEX_SCRIPT = "font.complex_script"
    FONT_SIZE = "font.size_pt"
    FONT_BOLD = "font.bold"
    FONT_ITALIC = "font.italic"
    FONT_UNDERLINE = "font.underline"
    FONT_COLOR = "font.color_hex"
    PARAGRAPH_ALIGNMENT = "paragraph.alignment"
    PARAGRAPH_LINE_SPACING = "paragraph.line_spacing"
    PARAGRAPH_SPACE_BEFORE = "paragraph.space_before_pt"
    PARAGRAPH_SPACE_AFTER = "paragraph.space_after_pt"
    PARAGRAPH_FIRST_LINE_INDENT = "paragraph.first_line_indent_pt"
    PARAGRAPH_LEFT_INDENT = "paragraph.left_indent_pt"
    PARAGRAPH_RIGHT_INDENT = "paragraph.right_indent_pt"


class FontSpec(StrictModel):
    east_asia: str | None = None
    ascii: str | None = None
    high_ansi: str | None = None
    complex_script: str | None = None
    size_pt: float | None = Field(default=None, gt=0, le=200)
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    color_hex: str | None = None

    @field_validator("color_hex")
    @classmethod
    def normalize_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.removeprefix("#").upper()
        if len(cleaned) != 6 or any(ch not in "0123456789ABCDEF" for ch in cleaned):
            raise ValueError("color_hex must contain six hexadecimal characters")
        return cleaned


class LineSpacingSpec(StrictModel):
    mode: LineSpacingMode
    value: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_value(self) -> LineSpacingSpec:
        if self.mode in {LineSpacingMode.EXACT, LineSpacingMode.AT_LEAST} and self.value is None:
            raise ValueError("exact and at_least line spacing require a point value")
        if self.mode == LineSpacingMode.MULTIPLE and self.value is None:
            raise ValueError("multiple line spacing requires a multiplier")
        return self


class ParagraphSpec(StrictModel):
    alignment: Alignment | None = None
    line_spacing: LineSpacingSpec | None = None
    space_before_pt: float | None = Field(default=None, ge=0)
    space_after_pt: float | None = Field(default=None, ge=0)
    first_line_indent_pt: float | None = None
    hanging_indent_pt: float | None = Field(default=None, ge=0)
    left_indent_pt: float | None = None
    right_indent_pt: float | None = None
    keep_with_next: bool | None = None
    keep_lines_together: bool | None = None
    page_break_before: bool | None = None
    widow_orphan_control: bool | None = None

    @model_validator(mode="after")
    def mutually_exclusive_indent(self) -> ParagraphSpec:
        if self.first_line_indent_pt is not None and self.hanging_indent_pt is not None:
            raise ValueError("first_line_indent_pt and hanging_indent_pt are mutually exclusive")
        return self


class ForcePolicy(StrictModel):
    properties: set[FormattingProperty] = Field(default_factory=set)


class RoleFormattingSpec(StrictModel):
    font: FontSpec | None = None
    paragraph: ParagraphSpec | None = None
    style_name: str | None = None
    force: ForcePolicy = Field(default_factory=ForcePolicy)


class PageFormattingSpec(StrictModel):
    size: PageSize | None = None
    orientation: Orientation | None = None
    margin_top_mm: float | None = Field(default=None, ge=0, le=100)
    margin_bottom_mm: float | None = Field(default=None, ge=0, le=100)
    margin_left_mm: float | None = Field(default=None, ge=0, le=100)
    margin_right_mm: float | None = Field(default=None, ge=0, le=100)
    header_distance_mm: float | None = Field(default=None, ge=0, le=100)
    footer_distance_mm: float | None = Field(default=None, ge=0, le=100)
    preserve_existing_landscape_sections: bool = True
    force_orientation_all_sections: bool = False


class DocumentFormattingSpec(StrictModel):
    page: PageFormattingSpec = Field(default_factory=PageFormattingSpec)


class TableFormattingSpec(StrictModel):
    alignment: Alignment | None = None
    width_policy: TableWidthPolicy = TableWidthPolicy.PRESERVE
    fixed_width_mm: float | None = Field(default=None, gt=0)
    cell_vertical_alignment: Literal["top", "center", "bottom"] | None = None
    font: FontSpec | None = None
    paragraph: ParagraphSpec | None = None
    grid_borders: bool = False
    repeat_header_row: bool = False
    prevent_row_split: bool = False
    adaptive_column_widths: bool = False
    min_column_width_mm: float = Field(default=8, gt=0, le=50)
    adaptive_font_size: bool = False
    min_font_size_pt: float = Field(default=8.5, gt=0, le=20)

    @model_validator(mode="after")
    def validate_fixed_width(self) -> TableFormattingSpec:
        if self.width_policy == TableWidthPolicy.FIXED and self.fixed_width_mm is None:
            raise ValueError("fixed width policy requires fixed_width_mm")
        return self


class FigureFormattingSpec(StrictModel):
    center_image_only_paragraphs: bool = False


class HeaderFooterFormattingSpec(StrictModel):
    font: FontSpec | None = None
    paragraph: ParagraphSpec | None = None


class PageNumberSpec(StrictModel):
    enabled: bool = False
    location: Literal["footer"] = "footer"
    alignment: Alignment = Alignment.CENTER
    number_format: Literal["decimal"] = "decimal"
    start_at: int | None = Field(default=None, ge=0)


class VisualCleanupSpec(StrictModel):
    text_color_hex: str | None = None
    remove_text_highlight: bool = False
    remove_character_shading: bool = False
    remove_paragraph_shading: bool = False
    remove_table_cell_shading: bool = False
    remove_page_background: bool = False

    @field_validator("text_color_hex")
    @classmethod
    def normalize_text_color(cls, value: str | None) -> str | None:
        return FontSpec.normalize_color(value)


class AutoLayoutSpec(StrictModel):
    """Deterministic structural layout applied before role-based formatting.

    Structural edits are deliberately conservative: only plain body paragraphs may be split,
    and every split is followed by a fresh local role analysis and content-integrity check.
    """

    enabled: bool = False
    split_body_paragraphs: bool = True
    split_on_manual_breaks: bool = True
    target_body_chars: int = Field(default=280, ge=80, le=2_000)
    max_body_chars: int = Field(default=480, ge=120, le=4_000)

    @model_validator(mode="after")
    def validate_body_lengths(self) -> AutoLayoutSpec:
        if self.target_body_chars > self.max_body_chars:
            raise ValueError("target_body_chars cannot exceed max_body_chars")
        return self


class FormattingBehavior(StrictModel):
    preserve_inline_emphasis: bool = True
    preserve_hyperlinks: bool = True
    preserve_fields: bool = True
    preserve_equations: bool = True
    preserve_unknown_xml: bool = True
    normalize_direct_paragraph_formatting: bool = True
    normalize_direct_run_formatting: bool = True
    preserve_right_aligned_signatures: bool = False
    apply_to_unknown_roles: bool = False
    unknown_role_fallback: SemanticRole = SemanticRole.BODY
    validation_passes: int = Field(default=2, ge=1, le=3)
    auto_repair: bool = True


class SpecSource(StrictModel):
    type: SpecSourceType = SpecSourceType.STRUCTURED
    preset_id: str | None = None
    instruction_hash: str | None = None
    compiler_version: str | None = None
    provider: str | None = None
    model: str | None = None
    assumptions: list[str] = Field(default_factory=list)


class RulePackClaimLevel(StrEnum):
    GENERIC = "generic"
    REFERENCE = "reference"
    VERIFIED = "verified"


class RulePackReference(StrictModel):
    title: str
    url: str
    version: str | None = None


class RulePackMetadata(StrictModel):
    pack_version: str
    claim_level: RulePackClaimLevel
    scope_label: str
    maintained_by: str
    last_reviewed_on: date
    source_references: list[RulePackReference]
    covered_capabilities: list[str]
    limitations: list[str]


class FormattingSpec(StrictModel):
    schema_version: Literal["formatting-spec.v1"] = "formatting-spec.v1"
    document: DocumentFormattingSpec | None = None
    baseline: RoleFormattingSpec | None = None
    roles: dict[SemanticRole, RoleFormattingSpec] = Field(default_factory=dict)
    tables: TableFormattingSpec | None = None
    figures: FigureFormattingSpec | None = None
    headers: HeaderFooterFormattingSpec | None = None
    footers: HeaderFooterFormattingSpec | None = None
    page_numbers: PageNumberSpec | None = None
    visual_cleanup: VisualCleanupSpec | None = None
    auto_layout: AutoLayoutSpec = Field(default_factory=AutoLayoutSpec)
    behavior: FormattingBehavior = Field(default_factory=FormattingBehavior)
    source: SpecSource = Field(default_factory=SpecSource)


def normalize_font_size(value: str | float | int) -> float:
    if isinstance(value, int | float):
        if value <= 0:
            raise ValueError("font size must be positive")
        return float(value)
    cleaned = value.strip()
    if cleaned in CHINESE_FONT_SIZES_PT:
        return CHINESE_FONT_SIZES_PT[cleaned]
    if cleaned.lower().endswith("pt"):
        return float(cleaned[:-2].strip())
    return float(cleaned)


def default_academic_spec() -> FormattingSpec:
    body_font = FontSpec(
        east_asia="宋体",
        ascii="Times New Roman",
        high_ansi="Times New Roman",
        complex_script="Times New Roman",
        size_pt=12,
    )
    body_paragraph = ParagraphSpec(
        alignment=Alignment.JUSTIFY,
        line_spacing=LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=1.5),
        first_line_indent_pt=24,
        space_before_pt=0,
        space_after_pt=0,
    )
    family_force = {
        FormattingProperty.FONT_EAST_ASIA,
        FormattingProperty.FONT_ASCII,
        FormattingProperty.FONT_HIGH_ANSI,
        FormattingProperty.FONT_COMPLEX_SCRIPT,
        FormattingProperty.FONT_SIZE,
    }
    roles: dict[SemanticRole, RoleFormattingSpec] = {
        SemanticRole.TITLE: _front_matter_role(
            east_asia="黑体",
            size=22,
            bold=True,
            space_before=0,
            space_after=18,
        ),
        SemanticRole.SUBTITLE: _front_matter_role(
            east_asia="黑体",
            size=15,
            bold=True,
            space_before=0,
            space_after=12,
        ),
        SemanticRole.AUTHOR_INFO: _front_matter_role(
            east_asia="宋体",
            size=12,
            bold=False,
            space_before=0,
            space_after=12,
        ),
        SemanticRole.ABSTRACT_HEADING: _heading_role(14, Alignment.CENTER, 12, 6),
        SemanticRole.BODY: RoleFormattingSpec(
            font=body_font,
            paragraph=body_paragraph,
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.ABSTRACT_BODY: RoleFormattingSpec(
            font=body_font,
            paragraph=body_paragraph,
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.APPENDIX_BODY: RoleFormattingSpec(
            font=body_font,
            paragraph=body_paragraph,
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.HEADING_1: _heading_role(16, Alignment.CENTER, 18, 12),
        SemanticRole.HEADING_2: _heading_role(14, Alignment.LEFT, 12, 6),
        SemanticRole.HEADING_3: _heading_role(12, Alignment.LEFT, 9, 3),
        SemanticRole.HEADING_4: _heading_role(12, Alignment.LEFT, 6, 3),
        SemanticRole.KEYWORDS: RoleFormattingSpec(
            font=body_font,
            paragraph=ParagraphSpec(
                alignment=Alignment.LEFT,
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=1.5),
                first_line_indent_pt=0,
                space_before_pt=0,
                space_after_pt=6,
            ),
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.BLOCKQUOTE: RoleFormattingSpec(
            font=FontSpec(
                east_asia="宋体",
                ascii="Times New Roman",
                high_ansi="Times New Roman",
                complex_script="Times New Roman",
                size_pt=11,
            ),
            paragraph=ParagraphSpec(
                alignment=Alignment.JUSTIFY,
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1),
                left_indent_pt=24,
                right_indent_pt=24,
                space_before_pt=6,
                space_after_pt=6,
            ),
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.LIST_ITEM: RoleFormattingSpec(
            font=body_font,
            paragraph=ParagraphSpec(
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=1.5),
                space_before_pt=0,
                space_after_pt=0,
            ),
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.FIGURE_CAPTION: _caption_role(),
        SemanticRole.TABLE_CAPTION: _caption_role(),
        SemanticRole.BIBLIOGRAPHY_HEADING: _heading_role(16, Alignment.CENTER, 18, 12),
        SemanticRole.BIBLIOGRAPHY_ENTRY: RoleFormattingSpec(
            font=body_font,
            paragraph=ParagraphSpec(
                alignment=Alignment.JUSTIFY,
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1),
                hanging_indent_pt=21,
            ),
            force=ForcePolicy(properties=family_force),
        ),
        SemanticRole.APPENDIX_HEADING: _heading_role(16, Alignment.CENTER, 18, 12),
    }
    return FormattingSpec(
        document=DocumentFormattingSpec(
            page=PageFormattingSpec(
                size=PageSize.A4,
                orientation=Orientation.PORTRAIT,
                margin_top_mm=25,
                margin_bottom_mm=25,
                margin_left_mm=30,
                margin_right_mm=25,
            )
        ),
        roles=roles,
        tables=TableFormattingSpec(
            font=FontSpec(**body_font.model_dump()), paragraph=ParagraphSpec()
        ),
        figures=FigureFormattingSpec(center_image_only_paragraphs=True),
        auto_layout=AutoLayoutSpec(enabled=True),
        source=SpecSource(type=SpecSourceType.PRESET, preset_id="generic-academic-cn"),
    )


def default_cleanup_spec() -> FormattingSpec:
    """A deterministic, model-free preset for normalizing visually noisy documents."""

    academic = default_academic_spec()
    forced_font_properties = {
        FormattingProperty.FONT_EAST_ASIA,
        FormattingProperty.FONT_ASCII,
        FormattingProperty.FONT_HIGH_ANSI,
        FormattingProperty.FONT_COMPLEX_SCRIPT,
        FormattingProperty.FONT_SIZE,
        FormattingProperty.FONT_BOLD,
        FormattingProperty.FONT_ITALIC,
        FormattingProperty.FONT_UNDERLINE,
        FormattingProperty.FONT_COLOR,
    }
    emphasized_roles = {
        SemanticRole.TITLE,
        SemanticRole.SUBTITLE,
        SemanticRole.ABSTRACT_HEADING,
        SemanticRole.HEADING_1,
        SemanticRole.HEADING_2,
        SemanticRole.HEADING_3,
        SemanticRole.HEADING_4,
        SemanticRole.BIBLIOGRAPHY_HEADING,
        SemanticRole.APPENDIX_HEADING,
    }

    def clean_font(size_pt: float, *, bold: bool = False) -> FontSpec:
        return FontSpec(
            east_asia="宋体",
            ascii="Times New Roman",
            high_ansi="Times New Roman",
            complex_script="Times New Roman",
            size_pt=size_pt,
            bold=bold,
            italic=False,
            underline=False,
            color_hex="000000",
        )

    roles: dict[SemanticRole, RoleFormattingSpec] = {}
    for role, role_spec in academic.roles.items():
        size_pt = (
            role_spec.font.size_pt
            if role_spec.font is not None and role_spec.font.size_pt is not None
            else 12
        )
        roles[role] = role_spec.model_copy(
            update={
                "font": clean_font(size_pt, bold=role in emphasized_roles),
                "force": ForcePolicy(properties=forced_font_properties),
            }
        )

    # Word displays a small black square beside paragraphs with explicit
    # pagination controls when formatting marks are visible.  A cleanup preset
    # should not add those markers to every paragraph, so explicitly disable the
    # inherited academic keep-lines/keep-next settings for every cleanup role.
    for role_spec in roles.values():
        if role_spec.paragraph is None:
            continue
        role_spec.paragraph.keep_with_next = False
        role_spec.paragraph.keep_lines_together = False
        role_spec.paragraph.page_break_before = False
        role_spec.paragraph.widow_orphan_control = False

    body = roles[SemanticRole.BODY]
    list_role = roles.get(SemanticRole.LIST_ITEM)
    if list_role is not None and list_role.paragraph is not None:
        list_role.paragraph.line_spacing = LineSpacingSpec(
            mode=LineSpacingMode.MULTIPLE,
            value=1.25,
        )
        list_role.paragraph.space_after_pt = 3
    return FormattingSpec(
        document=DocumentFormattingSpec(
            page=PageFormattingSpec(
                size=PageSize.A4,
                orientation=Orientation.PORTRAIT,
                margin_top_mm=20,
                margin_bottom_mm=20,
                margin_left_mm=20,
                margin_right_mm=20,
                header_distance_mm=15,
                footer_distance_mm=15,
                preserve_existing_landscape_sections=False,
                force_orientation_all_sections=True,
            )
        ),
        baseline=RoleFormattingSpec(
            font=body.font,
            paragraph=body.paragraph,
            force=ForcePolicy(properties=forced_font_properties),
        ),
        roles=roles,
        tables=TableFormattingSpec(
            alignment=Alignment.CENTER,
            width_policy=TableWidthPolicy.FIT_PRINTABLE_WIDTH,
            cell_vertical_alignment="center",
            font=clean_font(10),
            paragraph=ParagraphSpec(
                alignment=Alignment.LEFT,
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1),
                space_before_pt=0,
                space_after_pt=0,
            ),
            grid_borders=True,
            repeat_header_row=True,
            prevent_row_split=True,
            adaptive_column_widths=True,
            adaptive_font_size=True,
            min_font_size_pt=8.5,
        ),
        figures=FigureFormattingSpec(center_image_only_paragraphs=True),
        headers=HeaderFooterFormattingSpec(
            font=clean_font(9),
            paragraph=ParagraphSpec(
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1),
                space_before_pt=0,
                space_after_pt=0,
            ),
        ),
        footers=HeaderFooterFormattingSpec(
            font=clean_font(9),
            paragraph=ParagraphSpec(
                line_spacing=LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1),
                space_before_pt=0,
                space_after_pt=0,
            ),
        ),
        visual_cleanup=VisualCleanupSpec(
            text_color_hex="000000",
            remove_text_highlight=True,
            remove_character_shading=True,
            remove_paragraph_shading=True,
            remove_table_cell_shading=True,
            remove_page_background=True,
        ),
        auto_layout=AutoLayoutSpec(enabled=True),
        behavior=FormattingBehavior(
            preserve_inline_emphasis=False,
            preserve_right_aligned_signatures=True,
            apply_to_unknown_roles=True,
        ),
        source=SpecSource(
            type=SpecSourceType.PRESET,
            preset_id="default-clean-cn",
            assumptions=[
                "All sections are normalized to A4 portrait with 20 mm margins.",
                "Chinese text uses SimSun, Latin text uses Times New Roman, "
                "and visible text is black.",
                "Highlights and Word character, paragraph, cell, and page backgrounds are removed.",
                "Titles remain distinguishable through conventional size and weight hierarchy.",
                "Tables receive light grid borders, repeating headers, and adaptive sizing.",
            ],
        ),
    )


def compact_cleanup_spec() -> FormattingSpec:
    """A compact profile for resumes, meeting notes, and operational documents."""

    spec = default_cleanup_spec().model_copy(deep=True)
    assert spec.document is not None
    spec.document.page.margin_top_mm = 16
    spec.document.page.margin_bottom_mm = 16
    spec.document.page.margin_left_mm = 18
    spec.document.page.margin_right_mm = 18
    for role in (SemanticRole.BODY, SemanticRole.ABSTRACT_BODY, SemanticRole.APPENDIX_BODY):
        role_spec = spec.roles.get(role)
        if role_spec is None:
            continue
        if role_spec.font is not None:
            role_spec.font.size_pt = 11
        role_spec.paragraph = ParagraphSpec(
            alignment=Alignment.LEFT,
            line_spacing=LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=1.25),
            space_before_pt=0,
            space_after_pt=4,
            first_line_indent_pt=0,
            keep_with_next=False,
            keep_lines_together=False,
            page_break_before=False,
            widow_orphan_control=False,
        )
    for role, size in (
        (SemanticRole.TITLE, 20),
        (SemanticRole.HEADING_1, 14),
        (SemanticRole.HEADING_2, 12),
        (SemanticRole.HEADING_3, 11),
        (SemanticRole.HEADING_4, 11),
    ):
        role_spec = spec.roles.get(role)
        if role_spec is None:
            continue
        if role_spec.font is not None:
            role_spec.font.size_pt = size
        role_spec.paragraph = ParagraphSpec(
            alignment=Alignment.LEFT,
            space_before_pt=8,
            space_after_pt=4,
            keep_with_next=False,
            keep_lines_together=False,
            page_break_before=False,
            widow_orphan_control=False,
        )
    if spec.baseline is not None:
        spec.baseline = spec.roles[SemanticRole.BODY].model_copy(deep=True)
    spec.source = SpecSource(
        type=SpecSourceType.PRESET,
        preset_id="compact-clean-cn",
        assumptions=[
            "Compact left-aligned hierarchy is used for resumes, minutes, and manuals.",
            "All visual cleanup and content-integrity safeguards remain enabled.",
        ],
    )
    return spec


def contract_cleanup_spec() -> FormattingSpec:
    """A conservative profile for contracts and other clause-led legal documents."""

    spec = default_cleanup_spec().model_copy(deep=True)
    for role in (SemanticRole.HEADING_1, SemanticRole.HEADING_2, SemanticRole.HEADING_3):
        role_spec = spec.roles.get(role)
        if role_spec is not None and role_spec.paragraph is not None:
            role_spec.paragraph.alignment = Alignment.LEFT
            role_spec.paragraph.keep_with_next = False
    spec.source = SpecSource(
        type=SpecSourceType.PRESET,
        preset_id="contract-clean-cn",
        assumptions=[
            "Contract articles remain left aligned and attached to the following clause body.",
            "All visual cleanup and content-integrity safeguards remain enabled.",
        ],
    )
    return spec


def wide_table_cleanup_spec() -> FormattingSpec:
    """A profile that preserves landscape sections for financial and data-heavy tables."""

    spec = default_cleanup_spec().model_copy(deep=True)
    assert spec.document is not None
    spec.document.page.preserve_existing_landscape_sections = True
    spec.document.page.force_orientation_all_sections = False
    spec.document.page.margin_left_mm = 15
    spec.document.page.margin_right_mm = 15
    if spec.tables is not None:
        spec.tables.min_column_width_mm = 7
        spec.tables.min_font_size_pt = 8
    spec.source = SpecSource(
        type=SpecSourceType.PRESET,
        preset_id="wide-table-clean-cn",
        assumptions=[
            "Existing landscape sections are preserved so wide tables stay readable.",
            "All visual cleanup and content-integrity safeguards remain enabled.",
        ],
    )
    return spec


class CleanupPresetCatalogItem(StrictModel):
    preset_id: str
    name: str
    description: str
    recommended_kinds: list[str]
    metadata: RulePackMetadata
    spec: FormattingSpec


def _generic_pack_metadata(*specific_limitations: str) -> RulePackMetadata:
    return RulePackMetadata(
        pack_version="1.0.0",
        claim_level=RulePackClaimLevel.GENERIC,
        scope_label="DocAlign 内置通用整理规则",
        maintained_by="DocAlign",
        last_reviewed_on=date(2026, 8, 29),
        source_references=[],
        covered_capabilities=[
            "page_layout",
            "document_typography",
            "role_typography",
            "table_formatting",
            "figure_formatting",
            "header_footer_formatting",
            "page_numbers",
            "document_text_color",
            "document_background_cleanup",
            "auto_layout",
        ],
        limitations=[
            "这是通用整理方案，不代表 GB/T 9704、高校、期刊或任何机构规则的完整合规。",
            "目录、脚注、尾注、批注和文本框目前只做内容保护，不执行专属格式规则。",
            *specific_limitations,
        ],
    )


def cleanup_preset_catalog() -> list[CleanupPresetCatalogItem]:
    return [
        CleanupPresetCatalogItem(
            preset_id="default-clean-cn",
            name="常规文档",
            description="A4 竖版、正文小四、1.5 倍行距，适合通知、论文和报告。",
            recommended_kinds=["government_document", "academic_paper", "report", "other"],
            metadata=_generic_pack_metadata(
                "会统一为 A4 竖版，不适合必须保留横向宽表或机构专用分节的文档。"
            ),
            spec=default_cleanup_spec(),
        ),
        CleanupPresetCatalogItem(
            preset_id="compact-clean-cn",
            name="紧凑信息",
            description="左对齐、较紧凑，适合简历、会议纪要和操作手册。",
            recommended_kinds=["resume", "meeting_minutes", "manual"],
            metadata=_generic_pack_metadata(
                "紧凑字号和间距是通用可读性选择，不代表招聘、会议或培训机构模板。"
            ),
            spec=compact_cleanup_spec(),
        ),
        CleanupPresetCatalogItem(
            preset_id="contract-clean-cn",
            name="合同条款",
            description="条款标题左对齐并与正文同页，适合法律合同。",
            recommended_kinds=["contract"],
            metadata=_generic_pack_metadata(
                "只整理合同版式，不审查条款内容、签署效力或任何法律合规要求。"
            ),
            spec=contract_cleanup_spec(),
        ),
        CleanupPresetCatalogItem(
            preset_id="wide-table-clean-cn",
            name="宽表优先",
            description="保留原有横版分节，适合财务报表和多列表格。",
            recommended_kinds=["financial_report"],
            metadata=_generic_pack_metadata(
                "只优化宽表版式，不验证会计准则、金额、公式或财务披露要求。"
            ),
            spec=wide_table_cleanup_spec(),
        ),
    ]


def _heading_role(
    size: float,
    alignment: Alignment,
    space_before: float,
    space_after: float,
) -> RoleFormattingSpec:
    forced = {
        FormattingProperty.FONT_EAST_ASIA,
        FormattingProperty.FONT_ASCII,
        FormattingProperty.FONT_HIGH_ANSI,
        FormattingProperty.FONT_COMPLEX_SCRIPT,
        FormattingProperty.FONT_SIZE,
        FormattingProperty.FONT_BOLD,
    }
    return RoleFormattingSpec(
        font=FontSpec(
            east_asia="黑体",
            ascii="Times New Roman",
            high_ansi="Times New Roman",
            complex_script="Times New Roman",
            size_pt=size,
            bold=True,
        ),
        paragraph=ParagraphSpec(
            alignment=alignment,
            space_before_pt=space_before,
            space_after_pt=space_after,
            keep_with_next=True,
        ),
        force=ForcePolicy(properties=forced),
    )


def _front_matter_role(
    *,
    east_asia: str,
    size: float,
    bold: bool,
    space_before: float,
    space_after: float,
) -> RoleFormattingSpec:
    return RoleFormattingSpec(
        font=FontSpec(
            east_asia=east_asia,
            ascii="Times New Roman",
            high_ansi="Times New Roman",
            complex_script="Times New Roman",
            size_pt=size,
            bold=bold,
        ),
        paragraph=ParagraphSpec(
            alignment=Alignment.CENTER,
            space_before_pt=space_before,
            space_after_pt=space_after,
            keep_with_next=True,
        ),
        force=ForcePolicy(
            properties={
                FormattingProperty.FONT_EAST_ASIA,
                FormattingProperty.FONT_ASCII,
                FormattingProperty.FONT_HIGH_ANSI,
                FormattingProperty.FONT_COMPLEX_SCRIPT,
                FormattingProperty.FONT_SIZE,
                FormattingProperty.FONT_BOLD,
            }
        ),
    )


def _caption_role() -> RoleFormattingSpec:
    return RoleFormattingSpec(
        font=FontSpec(east_asia="宋体", ascii="Times New Roman", size_pt=10.5),
        paragraph=ParagraphSpec(alignment=Alignment.CENTER, space_before_pt=3, space_after_pt=6),
        force=ForcePolicy(
            properties={
                FormattingProperty.FONT_EAST_ASIA,
                FormattingProperty.FONT_ASCII,
                FormattingProperty.FONT_SIZE,
            }
        ),
    )


def load_formatting_spec(path: Path) -> FormattingSpec:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    return FormattingSpec.model_validate(payload)


def merge_dicts(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge_dicts(current, value)
        else:
            result[key] = value
    return result


def _discard_overridden_paragraph_indent(
    merged_container: dict[str, object],
    overriding_container: dict[str, object],
) -> None:
    """Keep only the indent mode explicitly selected by the later rule."""

    merged_paragraph = merged_container.get("paragraph")
    overriding_paragraph = overriding_container.get("paragraph")
    if not isinstance(merged_paragraph, dict) or not isinstance(overriding_paragraph, dict):
        return
    if overriding_paragraph.get("first_line_indent_pt") is not None:
        merged_paragraph.pop("hanging_indent_pt", None)
    elif overriding_paragraph.get("hanging_indent_pt") is not None:
        merged_paragraph.pop("first_line_indent_pt", None)


def _reconcile_spec_paragraph_indents(
    merged: dict[str, object], override: dict[str, object]
) -> None:
    """Apply last-writer-wins semantics to mutually exclusive indent settings."""

    for key in ("baseline", "tables", "headers", "footers"):
        merged_container = merged.get(key)
        overriding_container = override.get(key)
        if isinstance(merged_container, dict) and isinstance(overriding_container, dict):
            _discard_overridden_paragraph_indent(merged_container, overriding_container)

    merged_roles = merged.get("roles")
    overriding_roles = override.get("roles")
    if not isinstance(merged_roles, dict) or not isinstance(overriding_roles, dict):
        return
    for role, overriding_role in overriding_roles.items():
        merged_role = merged_roles.get(role)
        if isinstance(merged_role, dict) and isinstance(overriding_role, dict):
            _discard_overridden_paragraph_indent(merged_role, overriding_role)


def resolve_role_spec(spec: FormattingSpec, role: SemanticRole) -> RoleFormattingSpec | None:
    """Resolve the document-wide text baseline with a role-specific override."""

    override = spec.roles.get(role)
    if override is None and role == SemanticRole.LIST_ITEM:
        override = spec.roles.get(SemanticRole.BODY)
    if spec.baseline is None:
        return override
    if override is None:
        return spec.baseline
    payload = merge_dicts(
        spec.baseline.model_dump(mode="json", exclude_none=True),
        override.model_dump(mode="json", exclude_none=True),
    )
    _discard_overridden_paragraph_indent(
        payload,
        override.model_dump(mode="json", exclude_none=True),
    )
    payload["force"] = ForcePolicy(
        properties=spec.baseline.force.properties | override.force.properties
    ).model_dump(mode="json")
    return RoleFormattingSpec.model_validate(payload)


def merge_specs(*specs: FormattingSpec) -> FormattingSpec:
    if not specs:
        return default_academic_spec()
    payload: dict[str, object] = {}
    assumptions: list[str] = []
    for index, spec in enumerate(specs):
        override = spec.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=index > 0,
        )
        payload = merge_dicts(payload, override)
        _reconcile_spec_paragraph_indents(payload, override)
        assumptions.extend(spec.source.assumptions)
    latest_source = specs[-1].source
    payload["source"] = SpecSource(
        type=SpecSourceType.MERGED,
        preset_id=next(
            (spec.source.preset_id for spec in specs if spec.source.preset_id),
            None,
        ),
        instruction_hash=latest_source.instruction_hash,
        compiler_version=latest_source.compiler_version,
        provider=latest_source.provider,
        model=latest_source.model,
        assumptions=list(dict.fromkeys(assumptions)),
    ).model_dump(mode="json")
    return FormattingSpec.model_validate(payload)
