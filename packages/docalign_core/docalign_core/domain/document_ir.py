from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.enums import AnalysisMode, DocumentKind, RoleSource, SemanticRole


class DocumentWarning(StrictModel):
    code: str
    message: str
    node_id: str | None = None
    details: dict[str, object] = Field(default_factory=dict)


class RunFormatSnapshot(StrictModel):
    ascii_font: str | None = None
    high_ansi_font: str | None = None
    east_asia_font: str | None = None
    complex_script_font: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    color_hex: str | None = None


class ParagraphFormatSnapshot(StrictModel):
    alignment: str | None = None
    line_spacing: float | None = None
    line_spacing_rule: str | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    first_line_indent_pt: float | None = None
    left_indent_pt: float | None = None
    right_indent_pt: float | None = None
    keep_with_next: bool | None = None
    keep_lines_together: bool | None = None
    page_break_before: bool | None = None


class RunIR(StrictModel):
    run_id: str
    locator: str = ""
    text: str
    formatting: RunFormatSnapshot = Field(default_factory=RunFormatSnapshot)
    protected: bool = False
    protection_reason: str | None = None


class NumberingInfo(StrictModel):
    num_id: int | None = None
    level: int | None = None


class ParagraphIR(StrictModel):
    kind: Literal["paragraph"] = "paragraph"
    node_id: str
    locator: str = ""
    index: int
    text: str
    current_style_name: str | None = None
    detected_role: SemanticRole = SemanticRole.UNKNOWN
    role_confidence: float = 0.0
    role_source: RoleSource = RoleSource.FALLBACK
    role_evidence: list[str] = Field(default_factory=list)
    numbering: NumberingInfo | None = None
    formatting: ParagraphFormatSnapshot = Field(default_factory=ParagraphFormatSnapshot)
    runs: list[RunIR] = Field(default_factory=list)
    contains_drawing: bool = False
    contains_equation: bool = False
    contains_field: bool = False
    contains_hyperlink: bool = False
    contains_bookmark: bool = False
    contains_content_control: bool = False
    is_empty: bool = False


class TableIR(StrictModel):
    kind: Literal["table"] = "table"
    node_id: str
    locator: str = ""
    index: int
    rows: int
    columns_estimate: int
    cell_texts: list[list[str]] = Field(default_factory=list)
    merged_cells_present: bool = False
    nested_tables_present: bool = False
    width_estimate_twips: int | None = None
    style_name: str | None = None
    detected_role: str = "table"


class UnsupportedBlockIR(StrictModel):
    kind: Literal["unsupported"] = "unsupported"
    node_id: str
    locator: str = ""
    index: int
    qname: str
    text_preview: str = ""


BlockIR = Annotated[ParagraphIR | TableIR | UnsupportedBlockIR, Field(discriminator="kind")]


class SectionIR(StrictModel):
    index: int
    locator: str = ""
    page_width_twips: int | None = None
    page_height_twips: int | None = None
    orientation: str | None = None
    margin_top_twips: int | None = None
    margin_bottom_twips: int | None = None
    margin_left_twips: int | None = None
    margin_right_twips: int | None = None
    header_distance_twips: int | None = None
    footer_distance_twips: int | None = None
    different_first_page: bool = False


class HeaderFooterIR(StrictModel):
    part: Literal["header", "footer"]
    section_index: int
    locator: str = ""
    variant: Literal["default", "first", "even"] = "default"
    linked_to_previous: bool = False
    paragraph_texts: list[str] = Field(default_factory=list)


class PackagePartIR(StrictModel):
    path: str
    compressed_size: int
    uncompressed_size: int
    sha256: str


class RelationshipIR(StrictModel):
    source_part: str
    relationship_id: str
    relationship_type: str
    target: str
    external: bool = False

    def signature(self) -> str:
        return "|".join(
            (
                self.source_part,
                self.relationship_id,
                self.relationship_type,
                self.target,
                "external" if self.external else "internal",
            )
        )


class ContentFingerprint(StrictModel):
    paragraph_texts: list[str] = Field(default_factory=list)
    table_cell_texts: list[list[str]] = Field(default_factory=list)
    header_footer_texts: list[str] = Field(default_factory=list)
    field_instructions: list[str] = Field(default_factory=list)
    bookmark_names: list[str] = Field(default_factory=list)
    image_hashes: list[str] = Field(default_factory=list)
    relationship_signatures: list[str] = Field(default_factory=list)
    block_kinds: list[str] = Field(default_factory=list)
    unsupported_block_signatures: list[str] = Field(default_factory=list)
    section_count: int = 0
    table_count: int = 0
    image_count: int = 0
    digest: str


class DocumentMetadata(StrictModel):
    paragraph_count: int = 0
    table_count: int = 0
    image_count: int = 0
    existing_styles: list[str] = Field(default_factory=list)
    package_part_count: int = 0
    source_size_bytes: int = 0


class DocumentIR(StrictModel):
    schema_version: Literal["document-ir.v1"] = "document-ir.v1"
    document_id: str
    source_filename: str
    source_sha256: str
    sections: list[SectionIR] = Field(default_factory=list)
    blocks: list[BlockIR] = Field(default_factory=list)
    headers_footers: list[HeaderFooterIR] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    package_parts: list[PackagePartIR] = Field(default_factory=list)
    content_fingerprint: ContentFingerprint
    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)
    warnings: list[DocumentWarning] = Field(default_factory=list)


class RoleOverride(StrictModel):
    node_id: str
    role: SemanticRole


class AnalysisSummary(StrictModel):
    paragraph_count: int
    table_count: int
    image_count: int
    unknown_count: int
    role_counts: dict[str, int]
    existing_styles: list[str]
    analysis_mode: AnalysisMode = AnalysisMode.DETERMINISTIC
    document_kind: DocumentKind | None = None
    document_kind_confidence: float = Field(default=0.0, ge=0, le=1)
    model_reviewed_paragraphs: int = Field(default=0, ge=0)
    model_provider: str | None = None
    model_name: str | None = None


class AnalysisResult(StrictModel):
    document_ir: DocumentIR
    summary: AnalysisSummary
    warnings: list[DocumentWarning] = Field(default_factory=list)
