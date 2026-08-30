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
    TEMPLATE = "template"
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
    reference_filename: str | None = None
    reference_sha256: str | None = None
    assumptions: list[str] = Field(default_factory=list)


class RulePackClaimLevel(StrEnum):
    GENERIC = "generic"
    REFERENCE = "reference"
    VERIFIED = "verified"


class RulePackCoverageStatus(StrEnum):
    AUTOMATED = "automated"
    MANUAL_REVIEW = "manual_review"
    UNSUPPORTED = "unsupported"


class RulePackReference(StrictModel):
    title: str
    url: str
    version: str | None = None


class RulePackCoverageItem(StrictModel):
    requirement_id: str
    requirement: str
    status: RulePackCoverageStatus
    implementation_note: str


class RulePackAcceptanceEvidence(StrictModel):
    fixture_id: str
    last_passed_on: date
    automated_checks: list[str] = Field(min_length=1)
    manual_checks: list[str] = Field(default_factory=list)


class RulePackMetadata(StrictModel):
    pack_version: str
    claim_level: RulePackClaimLevel
    scope_label: str
    maintained_by: str
    last_reviewed_on: date
    source_references: list[RulePackReference]
    covered_capabilities: list[str]
    limitations: list[str]
    coverage_items: list[RulePackCoverageItem] = Field(default_factory=list)
    acceptance_evidence: RulePackAcceptanceEvidence | None = None

    @model_validator(mode="after")
    def validate_evidence_for_non_generic_pack(self) -> RulePackMetadata:
        if self.claim_level == RulePackClaimLevel.GENERIC:
            return self
        if not self.source_references:
            raise ValueError("reference and verified rule packs require source references")
        if not self.coverage_items:
            raise ValueError("reference and verified rule packs require clause coverage")
        if self.acceptance_evidence is None:
            raise ValueError("reference and verified rule packs require acceptance evidence")
        return self


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


def _institution_font(
    east_asia: str,
    size_pt: float,
    *,
    ascii_font: str = "Times New Roman",
    bold: bool | None = None,
) -> FontSpec:
    return FontSpec(
        east_asia=east_asia,
        ascii=ascii_font,
        high_ansi=ascii_font,
        complex_script=ascii_font,
        size_pt=size_pt,
        bold=bold,
        color_hex="000000",
    )


def _institution_force(*, bold: bool | None = None) -> ForcePolicy:
    properties = {
        FormattingProperty.FONT_EAST_ASIA,
        FormattingProperty.FONT_ASCII,
        FormattingProperty.FONT_HIGH_ANSI,
        FormattingProperty.FONT_COMPLEX_SCRIPT,
        FormattingProperty.FONT_SIZE,
        FormattingProperty.FONT_COLOR,
    }
    if bold is not None:
        properties.add(FormattingProperty.FONT_BOLD)
    return ForcePolicy(properties=properties)


def _institution_role(
    east_asia: str,
    size_pt: float,
    *,
    ascii_font: str = "Times New Roman",
    bold: bool | None = None,
    alignment: Alignment | None = None,
    line_spacing: LineSpacingSpec | None = None,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
    first_line_indent_pt: float | None = None,
    keep_with_next: bool | None = None,
    page_break_before: bool | None = None,
) -> RoleFormattingSpec:
    return RoleFormattingSpec(
        font=_institution_font(
            east_asia,
            size_pt,
            ascii_font=ascii_font,
            bold=bold,
        ),
        paragraph=ParagraphSpec(
            alignment=alignment,
            line_spacing=line_spacing,
            space_before_pt=space_before_pt,
            space_after_pt=space_after_pt,
            first_line_indent_pt=first_line_indent_pt,
            keep_with_next=keep_with_next,
            page_break_before=page_break_before,
        ),
        force=_institution_force(bold=bold),
    )


def gbt_9704_body_reference_spec() -> FormattingSpec:
    """Executable subset of GB/T 9704—2012 for the document body.

    The standard contains fixed-position header, imprint, seal, odd/even page-number,
    print, and binding requirements that cannot be represented by FormattingSpec v1.
    """

    body_spacing = LineSpacingSpec(mode=LineSpacingMode.EXACT, value=28.95)
    body = _institution_role(
        "仿宋_GB2312",
        16,
        alignment=Alignment.JUSTIFY,
        line_spacing=body_spacing,
        space_before_pt=0,
        space_after_pt=0,
        first_line_indent_pt=32,
    )
    return FormattingSpec(
        document=DocumentFormattingSpec(
            page=PageFormattingSpec(
                size=PageSize.A4,
                orientation=Orientation.PORTRAIT,
                margin_top_mm=37,
                margin_bottom_mm=35,
                margin_left_mm=28,
                margin_right_mm=26,
                preserve_existing_landscape_sections=True,
                force_orientation_all_sections=False,
            )
        ),
        roles={
            SemanticRole.TITLE: _institution_role(
                "方正小标宋简体",
                22,
                alignment=Alignment.CENTER,
                line_spacing=body_spacing,
                space_before_pt=0,
                space_after_pt=0,
                keep_with_next=True,
            ),
            SemanticRole.BODY: body,
            SemanticRole.HEADING_1: _institution_role(
                "黑体",
                16,
                alignment=Alignment.LEFT,
                line_spacing=body_spacing,
                space_before_pt=0,
                space_after_pt=0,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_2: _institution_role(
                "楷体_GB2312",
                16,
                alignment=Alignment.LEFT,
                line_spacing=body_spacing,
                space_before_pt=0,
                space_after_pt=0,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_3: _institution_role(
                "仿宋_GB2312",
                16,
                alignment=Alignment.LEFT,
                line_spacing=body_spacing,
                space_before_pt=0,
                space_after_pt=0,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_4: _institution_role(
                "仿宋_GB2312",
                16,
                alignment=Alignment.LEFT,
                line_spacing=body_spacing,
                space_before_pt=0,
                space_after_pt=0,
                keep_with_next=True,
            ),
            SemanticRole.LIST_ITEM: body.model_copy(deep=True),
        },
        auto_layout=AutoLayoutSpec(enabled=False),
        behavior=FormattingBehavior(
            preserve_inline_emphasis=True,
            preserve_right_aligned_signatures=True,
            apply_to_unknown_roles=False,
        ),
        source=SpecSource(
            type=SpecSourceType.PRESET,
            preset_id="gbt-9704-2012-body-reference-cn",
            assumptions=[
                "Only the A4 body page geometry, title, body, and four structural levels "
                "are automated.",
                "The 225 mm text height is represented by 28.95 pt exact line spacing; "
                "22-line fit still requires visual review.",
                "Small-title-Song and GB2312 font families must be installed or explicitly "
                "substituted before delivery.",
            ],
        ),
    )


def nankai_thesis_2026_reference_spec() -> FormattingSpec:
    """Executable subset of Nankai University's 2026 graduate thesis guide."""

    body_spacing = LineSpacingSpec(mode=LineSpacingMode.EXACT, value=20)
    compact_spacing = LineSpacingSpec(mode=LineSpacingMode.EXACT, value=16)
    single_spacing = LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1)
    body = _institution_role(
        "宋体",
        12,
        alignment=Alignment.JUSTIFY,
        line_spacing=body_spacing,
        space_before_pt=0,
        space_after_pt=0,
        first_line_indent_pt=24,
    )
    chapter = _institution_role(
        "黑体",
        16,
        bold=True,
        alignment=Alignment.LEFT,
        line_spacing=single_spacing,
        space_before_pt=24,
        space_after_pt=18,
        keep_with_next=True,
    )
    return FormattingSpec(
        document=DocumentFormattingSpec(
            page=PageFormattingSpec(
                size=PageSize.A4,
                orientation=Orientation.PORTRAIT,
                margin_top_mm=38,
                margin_bottom_mm=38,
                margin_left_mm=32,
                margin_right_mm=32,
                header_distance_mm=30,
                footer_distance_mm=30,
                preserve_existing_landscape_sections=True,
                force_orientation_all_sections=False,
            )
        ),
        roles={
            SemanticRole.BODY: body,
            SemanticRole.ABSTRACT_BODY: body.model_copy(deep=True),
            SemanticRole.KEYWORDS: body.model_copy(deep=True),
            SemanticRole.HEADING_1: chapter,
            SemanticRole.HEADING_2: _institution_role(
                "黑体",
                14,
                bold=True,
                alignment=Alignment.LEFT,
                line_spacing=single_spacing,
                space_before_pt=24,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_3: _institution_role(
                "黑体",
                13,
                alignment=Alignment.LEFT,
                line_spacing=single_spacing,
                space_before_pt=12,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_4: _institution_role(
                "黑体",
                12,
                alignment=Alignment.LEFT,
                line_spacing=single_spacing,
                space_before_pt=12,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.FIGURE_CAPTION: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.CENTER,
                line_spacing=single_spacing,
                space_before_pt=6,
                space_after_pt=12,
                keep_with_next=True,
            ),
            SemanticRole.TABLE_CAPTION: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.CENTER,
                line_spacing=single_spacing,
                space_before_pt=6,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.BIBLIOGRAPHY_HEADING: chapter.model_copy(deep=True),
            SemanticRole.BIBLIOGRAPHY_ENTRY: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.JUSTIFY,
                line_spacing=compact_spacing,
                space_before_pt=0,
                space_after_pt=0,
            ),
            SemanticRole.APPENDIX_HEADING: chapter.model_copy(deep=True),
            SemanticRole.APPENDIX_BODY: body.model_copy(deep=True),
        },
        tables=TableFormattingSpec(
            width_policy=TableWidthPolicy.PRESERVE,
            font=_institution_font("宋体", 10.5),
            paragraph=ParagraphSpec(
                line_spacing=single_spacing,
                space_before_pt=0,
                space_after_pt=0,
            ),
            prevent_row_split=True,
        ),
        figures=FigureFormattingSpec(center_image_only_paragraphs=True),
        auto_layout=AutoLayoutSpec(enabled=False),
        behavior=FormattingBehavior(apply_to_unknown_roles=False),
        source=SpecSource(
            type=SpecSourceType.PRESET,
            preset_id="nankai-thesis-2026-reference-cn",
            assumptions=[
                "The pack follows numeric heading mode with left-aligned chapter titles.",
                "Official cover, title page, declaration, table of contents, and "
                "section-specific page numbering remain template/manual work.",
                "English abstract heading requires Arial and is not changed because the "
                "current semantic role cannot distinguish it from the Chinese heading.",
            ],
        ),
    )


def bigc_master_thesis_2025_reference_spec() -> FormattingSpec:
    """Executable subset of BIGC's September 2025 master's thesis guide."""

    body_spacing = LineSpacingSpec(mode=LineSpacingMode.EXACT, value=20)
    compact_spacing = LineSpacingSpec(mode=LineSpacingMode.EXACT, value=16)
    single_spacing = LineSpacingSpec(mode=LineSpacingMode.SINGLE, value=1)
    body = _institution_role(
        "宋体",
        12,
        alignment=Alignment.JUSTIFY,
        line_spacing=body_spacing,
        space_before_pt=0,
        space_after_pt=0,
        first_line_indent_pt=24,
    )
    chapter = _institution_role(
        "黑体",
        16,
        alignment=Alignment.CENTER,
        line_spacing=single_spacing,
        space_before_pt=24,
        space_after_pt=18,
        keep_with_next=True,
    )
    return FormattingSpec(
        document=DocumentFormattingSpec(
            page=PageFormattingSpec(
                size=PageSize.A4,
                orientation=Orientation.PORTRAIT,
                margin_top_mm=30,
                margin_bottom_mm=25,
                margin_left_mm=25,
                margin_right_mm=25,
                header_distance_mm=16,
                footer_distance_mm=15,
                preserve_existing_landscape_sections=True,
                force_orientation_all_sections=False,
            )
        ),
        roles={
            SemanticRole.BODY: body,
            SemanticRole.ABSTRACT_BODY: body.model_copy(deep=True),
            SemanticRole.KEYWORDS: body.model_copy(deep=True),
            SemanticRole.HEADING_1: chapter,
            SemanticRole.HEADING_2: _institution_role(
                "黑体",
                15,
                alignment=Alignment.LEFT,
                line_spacing=single_spacing,
                space_before_pt=24,
                space_after_pt=18,
                keep_with_next=True,
            ),
            SemanticRole.HEADING_3: _institution_role(
                "黑体",
                14,
                alignment=Alignment.LEFT,
                line_spacing=single_spacing,
                space_before_pt=24,
                space_after_pt=18,
                keep_with_next=True,
            ),
            SemanticRole.FIGURE_CAPTION: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.CENTER,
                line_spacing=single_spacing,
                space_before_pt=6,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.TABLE_CAPTION: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.CENTER,
                line_spacing=single_spacing,
                space_before_pt=6,
                space_after_pt=6,
                keep_with_next=True,
            ),
            SemanticRole.BIBLIOGRAPHY_HEADING: chapter.model_copy(deep=True),
            SemanticRole.BIBLIOGRAPHY_ENTRY: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.JUSTIFY,
                line_spacing=compact_spacing,
                space_before_pt=0,
                space_after_pt=0,
            ),
            SemanticRole.APPENDIX_HEADING: chapter.model_copy(deep=True),
            SemanticRole.APPENDIX_BODY: _institution_role(
                "宋体",
                10.5,
                alignment=Alignment.JUSTIFY,
                line_spacing=compact_spacing,
                space_before_pt=0,
                space_after_pt=0,
            ),
        },
        tables=TableFormattingSpec(
            width_policy=TableWidthPolicy.PRESERVE,
            font=_institution_font("宋体", 10.5),
            paragraph=ParagraphSpec(
                line_spacing=single_spacing,
                space_before_pt=0,
                space_after_pt=0,
            ),
            repeat_header_row=True,
            prevent_row_split=True,
        ),
        figures=FigureFormattingSpec(center_image_only_paragraphs=True),
        auto_layout=AutoLayoutSpec(enabled=False),
        behavior=FormattingBehavior(apply_to_unknown_roles=False),
        source=SpecSource(
            type=SpecSourceType.PRESET,
            preset_id="bigc-master-thesis-2025-reference-cn",
            assumptions=[
                "The 10 mm binding line is not represented because FormattingSpec v1 "
                "has no independent gutter field.",
                "Official cover, title page, declarations, table of contents, page-number "
                "sections, and dynamic running headers remain template/manual work.",
                "English abstract heading is not changed because the current semantic role "
                "cannot distinguish it from the Chinese heading.",
            ],
        ),
    )


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


def _coverage(
    requirement_id: str,
    requirement: str,
    status: RulePackCoverageStatus,
    implementation_note: str,
) -> RulePackCoverageItem:
    return RulePackCoverageItem(
        requirement_id=requirement_id,
        requirement=requirement,
        status=status,
        implementation_note=implementation_note,
    )


def _reference_pack_metadata(
    *,
    scope_label: str,
    source_references: list[RulePackReference],
    covered_capabilities: list[str],
    limitations: list[str],
    coverage_items: list[RulePackCoverageItem],
    automated_checks: list[str],
    manual_checks: list[str],
) -> RulePackMetadata:
    return RulePackMetadata(
        pack_version="1.0.0",
        claim_level=RulePackClaimLevel.REFERENCE,
        scope_label=scope_label,
        maintained_by="DocAlign（依据公开规范整理，未经发布机构背书）",
        last_reviewed_on=date(2026, 8, 30),
        source_references=source_references,
        covered_capabilities=covered_capabilities,
        limitations=limitations,
        coverage_items=coverage_items,
        acceptance_evidence=RulePackAcceptanceEvidence(
            fixture_id="institutional-reference-smoke-v1",
            last_passed_on=date(2026, 8, 30),
            automated_checks=automated_checks,
            manual_checks=manual_checks,
        ),
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
        CleanupPresetCatalogItem(
            preset_id="gbt-9704-2012-body-reference-cn",
            name="GB/T 9704—2012 主体参考",
            description=(
                "执行 A4 版心、正文与四级结构字体；版头、版记、印章和奇偶页码仍需人工完成。"
            ),
            recommended_kinds=[],
            metadata=_reference_pack_metadata(
                scope_label="GB/T 9704—2012《党政机关公文格式》主体可执行子集",
                source_references=[
                    RulePackReference(
                        title="国家标准全文公开系统：GB/T 9704—2012",
                        url="https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=F3CC9BEF482524C895FDA7A08BB4A70E",
                        version="现行；2012-07-01 实施",
                    ),
                    RulePackReference(
                        title="南阳市人民政府公开标准正文",
                        url="https://www.nanyang.gov.cn/2021/05-08/53205.html",
                        version="GB/T 9704—2012",
                    ),
                ],
                covered_capabilities=[
                    "page_layout",
                    "role_typography",
                    "document_text_color",
                ],
                limitations=[
                    "这是标准主体排版参考，不是完整公文生成器或合规认证；选用前必须逐项查看覆盖矩阵。",
                    "版头、红色分隔线、发文字号、签发人、主送机关、附件说明、署名日期、印章和版记不自动编排。",
                    "奇偶页页码、一字线、空白页规则、22 行×28 字、双面印刷和装订只能人工复核。",
                    "方正小标宋简体、仿宋_GB2312、楷体_GB2312 必须在交付电脑安装，"
                    "否则 Word/WPS 会替代字体。",
                ],
                coverage_items=[
                    _coverage(
                        "5.1", "A4 成品幅面", RulePackCoverageStatus.AUTOMATED, "设置 A4 竖版。"
                    ),
                    _coverage(
                        "5.2.1",
                        "37 mm 天头、28 mm 订口和 156×225 mm 版心",
                        RulePackCoverageStatus.AUTOMATED,
                        "换算为上 37、下 35、左 28、右 26 mm。",
                    ),
                    _coverage(
                        "5.2.3",
                        "每面 22 行、每行 28 字",
                        RulePackCoverageStatus.MANUAL_REVIEW,
                        "以 28.95 磅固定行距近似版心高度；字数受实际字体与 Word 排版影响。",
                    ),
                    _coverage(
                        "7.3.1",
                        "主体标题二号小标宋居中",
                        RulePackCoverageStatus.AUTOMATED,
                        "对识别为主标题的段落设置 22 磅小标宋居中。",
                    ),
                    _coverage(
                        "7.3.3",
                        "正文三号仿宋、首行空二字及四级结构字体",
                        RulePackCoverageStatus.AUTOMATED,
                        "正文 16 磅仿宋、32 磅首行缩进；四级结构分别黑体、楷体、仿宋、仿宋。",
                    ),
                    _coverage(
                        "7.2/7.4",
                        "版头与版记固定要素",
                        RulePackCoverageStatus.UNSUPPORTED,
                        "当前语义模型与规则模型没有版头、版记和印章专属组件。",
                    ),
                    _coverage(
                        "7.5",
                        "奇偶页页码及一字线",
                        RulePackCoverageStatus.UNSUPPORTED,
                        "当前页码规则不能按奇偶页改变位置或绘制一字线。",
                    ),
                ],
                automated_checks=[
                    "输出通过 OOXML 内容与结构保护验证",
                    "A4 与四边版心换算值一致",
                    "正文仿宋三号、首行缩进与固定行距一致",
                    "四级结构字体层级一致",
                ],
                manual_checks=[
                    "安装目标字体后在 Word/WPS 检查每页 22 行×28 字",
                    "逐项核对版头、版记、印章、附件与奇偶页码",
                ],
            ),
            spec=gbt_9704_body_reference_spec(),
        ),
        CleanupPresetCatalogItem(
            preset_id="nankai-thesis-2026-reference-cn",
            name="南开大学论文 2026 参考",
            description="执行正文页边距、四级标题、正文、图表题和参考文献；封面、目录与分节页码保留人工流程。",
            recommended_kinds=[],
            metadata=_reference_pack_metadata(
                scope_label="南开大学研究生学位论文写作规范（2026版）可执行子集",
                source_references=[
                    RulePackReference(
                        title="南开大学研究生院：研究生学位论文写作规范",
                        url="https://graduate.nankai.edu.cn/2017/0222/c23238a56863/page.htm",
                        version="2026版",
                    ),
                ],
                covered_capabilities=[
                    "page_layout",
                    "role_typography",
                    "table_formatting",
                    "figure_formatting",
                ],
                limitations=[
                    "这是学校公开指导规范的部分自动化实现，未经南开大学审核、授权或背书。",
                    "封面、题名页、声明授权书、目录域、公式、引文标注和参考文献著录内容不自动生成或校对。",
                    "中文摘要与英文 Abstract 标题需要不同字体，当前统一语义角色无法"
                    "安全区分，因此标题保持原样。",
                    "前置大写罗马页码、正文阿拉伯页码、动态篇眉和双面印刷起始页需人工设置。",
                ],
                coverage_items=[
                    _coverage(
                        "4.1",
                        "A4 与上/下 38 mm、左/右 32 mm 页面设置",
                        RulePackCoverageStatus.AUTOMATED,
                        "同时设置页眉页脚距离 30 mm。",
                    ),
                    _coverage(
                        "4.5",
                        "中英文摘要正文与关键词 12 磅、20 磅行距",
                        RulePackCoverageStatus.AUTOMATED,
                        "按中西文字体分别设置宋体与 Times New Roman。",
                    ),
                    _coverage(
                        "4.5",
                        "中文摘要/Abstract 标题分别使用黑体/Arial 18 磅",
                        RulePackCoverageStatus.MANUAL_REVIEW,
                        "当前角色不能安全区分语言标题，保留原样并要求人工核对。",
                    ),
                    _coverage(
                        "4.7",
                        "章至三级标题层级与正文段落",
                        RulePackCoverageStatus.AUTOMATED,
                        "按数字编号模式执行 16/14/13/12 磅标题和 12 磅正文。",
                    ),
                    _coverage(
                        "4.7",
                        "图题与表题",
                        RulePackCoverageStatus.AUTOMATED,
                        "设置宋体五号居中及规范段前段后。",
                    ),
                    _coverage(
                        "4.8",
                        "参考文献与附录文字",
                        RulePackCoverageStatus.AUTOMATED,
                        "参考文献 10.5 磅/16 磅行距，附录正文 12 磅/20 磅行距。",
                    ),
                    _coverage(
                        "3.4",
                        "篇眉与前后置页码分节",
                        RulePackCoverageStatus.UNSUPPORTED,
                        "当前规则不能依据论文组成部分自动切换页码格式和篇眉文字。",
                    ),
                ],
                automated_checks=[
                    "输出通过 OOXML 内容与结构保护验证",
                    "页面与页眉页脚距离一致",
                    "四级标题字号、字体、对齐和间距一致",
                    "正文、题注、参考文献和附录段落属性一致",
                ],
                manual_checks=[
                    "使用学校官方封面、题名页与声明模板",
                    "核对摘要标题语言字体、目录、分节页码、篇眉、公式与引文",
                ],
            ),
            spec=nankai_thesis_2026_reference_spec(),
        ),
        CleanupPresetCatalogItem(
            preset_id="bigc-master-thesis-2025-reference-cn",
            name="北京印刷学院硕士论文 2025 参考",
            description="执行正文页面、三级标题、正文、图表题和参考文献；模板、装订线和分节页码仍需人工完成。",
            recommended_kinds=[],
            metadata=_reference_pack_metadata(
                scope_label="北京印刷学院硕士学位论文撰写规范（2025年9月）可执行子集",
                source_references=[
                    RulePackReference(
                        title="北京印刷学院研究生院：硕士学位论文撰写规范",
                        url="https://gs.bigc.edu.cn/docs/2025-09/1f20fd1dc1614ca7ba452d3f7df044d6.pdf",
                        version="2025年9月",
                    ),
                ],
                covered_capabilities=[
                    "page_layout",
                    "role_typography",
                    "table_formatting",
                    "figure_formatting",
                ],
                limitations=[
                    "这是学校公开规范的部分自动化实现，未经北京印刷学院审核、授权或背书。",
                    "10 mm 装订线没有独立写入；封面、书脊、题名页、声明和授权页必须使用学校模板。",
                    "中文摘要与英文 ABSTRACT 标题需要不同字体，当前统一语义角色无法"
                    "安全区分，因此标题保持原样。",
                    "前置罗马页码、正文阿拉伯页码、动态双侧页眉、三线表边框、公式和参考文献内容规范需人工复核。",
                ],
                coverage_items=[
                    _coverage(
                        "3.14",
                        "A4 与上 30、下 25、左/右 25 mm 页面设置",
                        RulePackCoverageStatus.AUTOMATED,
                        "同时设置页眉 16 mm、页脚 15 mm。",
                    ),
                    _coverage(
                        "3.14",
                        "10 mm 装订线",
                        RulePackCoverageStatus.UNSUPPORTED,
                        "FormattingSpec v1 尚无独立 gutter 字段，不以左页边距冒充装订线。",
                    ),
                    _coverage(
                        "3.4",
                        "摘要正文与关键词 12 磅、20 磅行距",
                        RulePackCoverageStatus.AUTOMATED,
                        "按中西文字体分别设置宋体与 Times New Roman。",
                    ),
                    _coverage(
                        "3.9",
                        "三级标题与正文段落",
                        RulePackCoverageStatus.AUTOMATED,
                        "执行 16/15/14 磅黑体标题和 12 磅正文。",
                    ),
                    _coverage(
                        "3.9.4/3.9.5",
                        "图题、表题和表内字号",
                        RulePackCoverageStatus.AUTOMATED,
                        "题注与表内文字设为宋体/Times New Roman 五号。",
                    ),
                    _coverage(
                        "3.10/3.11",
                        "参考文献与附录文字",
                        RulePackCoverageStatus.AUTOMATED,
                        "统一为 10.5 磅、16 磅固定行距。",
                    ),
                    _coverage(
                        "3.14",
                        "分节页码和动态页眉",
                        RulePackCoverageStatus.UNSUPPORTED,
                        "当前规则不能按前置/主体切换页码格式或填充左右不同的动态页眉。",
                    ),
                ],
                automated_checks=[
                    "输出通过 OOXML 内容与结构保护验证",
                    "页面与页眉页脚距离一致",
                    "三级标题字号、字体、对齐和间距一致",
                    "正文、题注、表格、参考文献和附录段落属性一致",
                ],
                manual_checks=[
                    "使用学校官方封面、书脊、题名页与声明模板并设置 10 mm 装订线",
                    "核对摘要标题、目录、三线表、分节页码、动态页眉、公式与引文",
                ],
            ),
            spec=bigc_master_thesis_2025_reference_spec(),
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
