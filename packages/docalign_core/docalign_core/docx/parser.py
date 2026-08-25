from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from docalign_core.docx.safety import SafetyLimits, sha256_file, validate_docx_package
from docalign_core.docx.xml_utils import field_instructions, local_name, logical_text
from docalign_core.domain.document_ir import (
    ContentFingerprint,
    DocumentIR,
    DocumentMetadata,
    DocumentWarning,
    HeaderFooterIR,
    NumberingInfo,
    ParagraphFormatSnapshot,
    ParagraphIR,
    RelationshipIR,
    RunFormatSnapshot,
    RunIR,
    SectionIR,
    TableIR,
    UnsupportedBlockIR,
)

BlockObject = tuple[Literal["paragraph", "table", "unsupported"], Any]
PROTECTED_TAGS = {
    qn("w:fldChar"),
    qn("w:instrText"),
    qn("m:oMath"),
    qn("m:oMathPara"),
    qn("w:drawing"),
    qn("w:bookmarkStart"),
    qn("w:bookmarkEnd"),
    qn("w:sdt"),
}


def iter_block_items(parent: Any) -> Iterator[BlockObject]:
    """Yield body child elements in source order without rebuilding XML."""

    if isinstance(parent, DocumentObject):
        parent_element = parent.element.body
        parent_proxy = parent
    else:
        parent_element = parent._element
        parent_proxy = parent
    for child in parent_element.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, parent_proxy)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, parent_proxy)
        elif child.tag == qn("w:sectPr"):
            continue
        else:
            yield "unsupported", child


def parse_docx(
    path: Path,
    *,
    document_id: str | None = None,
    safety_limits: SafetyLimits | None = None,
) -> DocumentIR:
    inspection = validate_docx_package(path, safety_limits)
    source_sha = sha256_file(path)
    document_id = document_id or f"doc_{source_sha[:12]}"
    document = Document(str(path))

    blocks: list[ParagraphIR | TableIR | UnsupportedBlockIR] = []
    warnings: list[DocumentWarning] = []
    paragraph_texts: list[str] = []
    table_cell_texts: list[list[str]] = []
    main_elements: list[Any] = []
    paragraph_count = 0
    table_count = 0
    unsupported_count = 0

    for index, (kind, value) in enumerate(iter_block_items(document)):
        if kind == "paragraph":
            paragraph = _parse_paragraph(
                value,
                index=index,
                part="main",
                locator=f"p{paragraph_count + 1}",
            )
            blocks.append(paragraph)
            paragraph_texts.append(paragraph.text)
            main_elements.append(value._p)
            paragraph_count += 1
        elif kind == "table":
            table = _parse_table(
                value,
                index=index,
                part="main",
                locator=f"t{table_count + 1}",
            )
            blocks.append(table)
            table_cell_texts.extend(table.cell_texts)
            main_elements.append(value._tbl)
            table_count += 1
        else:
            text = logical_text(value)
            node_id = _node_id("u", "main", index, text or local_name(value))
            blocks.append(
                UnsupportedBlockIR(
                    node_id=node_id,
                    locator=f"u{unsupported_count + 1}",
                    index=index,
                    qname=str(value.tag),
                    text_preview=text[:120],
                )
            )
            unsupported_count += 1
            main_elements.append(value)
            warnings.append(
                DocumentWarning(
                    code="UNKNOWN_OOXML_PRESERVED",
                    node_id=node_id,
                    message=f"Unsupported top-level OOXML block preserved: {local_name(value)}.",
                )
            )

    sections = [_parse_section(section, index) for index, section in enumerate(document.sections)]
    headers_footers, hf_texts, hf_elements = _parse_headers_footers(document)
    instructions = field_instructions([*main_elements, *hf_elements])
    bookmarks = _bookmark_names([*main_elements, *hf_elements])
    relationships = _parse_relationships(document)

    fingerprint_payload = {
        "paragraph_texts": paragraph_texts,
        "table_cell_texts": table_cell_texts,
        "header_footer_texts": hf_texts,
        "field_instructions": instructions,
        "bookmark_names": bookmarks,
        "image_hashes": inspection.image_hashes,
        "relationship_signatures": [item.signature() for item in relationships],
        "block_kinds": [block.kind for block in blocks],
        "unsupported_block_signatures": [
            f"{block.index}|{block.qname}|{block.text_preview}"
            for block in blocks
            if isinstance(block, UnsupportedBlockIR)
        ],
        "section_count": len(sections),
        "table_count": table_count,
        "image_count": len(inspection.image_hashes),
    }
    digest = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    fingerprint = ContentFingerprint(**fingerprint_payload, digest=digest)

    styles = sorted({style.name for style in document.styles if style.name})
    return DocumentIR(
        document_id=document_id,
        source_filename=path.name,
        source_sha256=source_sha,
        sections=sections,
        blocks=blocks,
        headers_footers=headers_footers,
        relationships=relationships,
        package_parts=inspection.parts,
        content_fingerprint=fingerprint,
        metadata=DocumentMetadata(
            paragraph_count=paragraph_count,
            table_count=table_count,
            image_count=len(inspection.image_hashes),
            existing_styles=styles,
            package_part_count=len(inspection.parts),
            source_size_bytes=path.stat().st_size,
        ),
        warnings=warnings,
    )


def _parse_paragraph(
    paragraph: Paragraph,
    *,
    index: int,
    part: str,
    locator: str,
) -> ParagraphIR:
    text = logical_text(paragraph._p)
    node_id = _node_id("p", part, index, text)
    contains_field = _contains_tag(paragraph._p, {qn("w:fldChar"), qn("w:instrText")})
    contains_equation = _contains_tag(paragraph._p, {qn("m:oMath"), qn("m:oMathPara")})
    contains_drawing = _contains_tag(paragraph._p, {qn("w:drawing"), qn("w:pict")})
    contains_hyperlink = _contains_tag(paragraph._p, {qn("w:hyperlink")})
    contains_bookmark = _contains_tag(paragraph._p, {qn("w:bookmarkStart"), qn("w:bookmarkEnd")})
    contains_content_control = _contains_tag(paragraph._p, {qn("w:sdt")})
    runs: list[RunIR] = []
    for run_index, run in enumerate(paragraph.runs):
        protected_tags = {node.tag for node in run._r.iter() if node.tag in PROTECTED_TAGS}
        reason = ",".join(sorted(etree.QName(tag).localname for tag in protected_tags)) or None
        runs.append(
            RunIR(
                run_id=f"{node_id}-r{run_index}",
                locator=f"{locator}.r{run_index + 1}",
                text=run.text,
                formatting=_run_format(run),
                protected=bool(protected_tags),
                protection_reason=reason,
            )
        )
    return ParagraphIR(
        node_id=node_id,
        locator=locator,
        index=index,
        text=text,
        current_style_name=paragraph.style.name if paragraph.style is not None else None,
        numbering=_numbering_info(paragraph),
        formatting=_paragraph_format(paragraph),
        runs=runs,
        contains_drawing=contains_drawing,
        contains_equation=contains_equation,
        contains_field=contains_field,
        contains_hyperlink=contains_hyperlink,
        contains_bookmark=contains_bookmark,
        contains_content_control=contains_content_control,
        is_empty=not text.strip() and not contains_drawing,
    )


def _parse_table(
    table: Table,
    *,
    index: int,
    part: str,
    locator: str,
) -> TableIR:
    cell_texts: list[list[str]] = []
    columns_estimate = 0
    for row in table._tbl.findall(qn("w:tr")):
        row_texts: list[str] = []
        column_count = 0
        for cell in row.findall(qn("w:tc")):
            row_texts.append(logical_text(cell))
            cell_properties = cell.find(qn("w:tcPr"))
            span = cell_properties.find(qn("w:gridSpan")) if cell_properties is not None else None
            column_count += int(span.get(qn("w:val"), "1")) if span is not None else 1
        columns_estimate = max(columns_estimate, column_count)
        cell_texts.append(row_texts)
    preview = "|".join(cell_texts[0]) if cell_texts else ""
    grid = table._tbl.tblGrid
    widths: list[int] = []
    if grid is not None:
        for column in grid.gridCol_lst:
            if column.w is not None:
                widths.append(int(column.w))
    return TableIR(
        node_id=_node_id("t", part, index, preview),
        locator=locator,
        index=index,
        rows=len(cell_texts),
        columns_estimate=columns_estimate,
        cell_texts=cell_texts,
        merged_cells_present=bool(table._tbl.xpath(".//w:gridSpan | .//w:vMerge")),
        nested_tables_present=bool(table._tbl.xpath(".//w:tc/w:tbl")),
        width_estimate_twips=sum(widths) if widths else None,
        style_name=table.style.name if table.style is not None else None,
    )


def _parse_section(section: Any, index: int) -> SectionIR:
    return SectionIR(
        index=index,
        locator=f"s{index + 1}",
        page_width_twips=_twips(section.page_width),
        page_height_twips=_twips(section.page_height),
        orientation=_enum_name(section.orientation),
        margin_top_twips=_twips(section.top_margin),
        margin_bottom_twips=_twips(section.bottom_margin),
        margin_left_twips=_twips(section.left_margin),
        margin_right_twips=_twips(section.right_margin),
        header_distance_twips=_twips(section.header_distance),
        footer_distance_twips=_twips(section.footer_distance),
        different_first_page=bool(section.different_first_page_header_footer),
    )


def _parse_headers_footers(
    document: DocumentObject,
) -> tuple[list[HeaderFooterIR], list[str], list[Any]]:
    items: list[HeaderFooterIR] = []
    texts: list[str] = []
    elements: list[Any] = []
    for index, section in enumerate(document.sections):
        for part_name, variant, container in (
            ("header", "default", section.header),
            ("header", "first", section.first_page_header),
            ("header", "even", section.even_page_header),
            ("footer", "default", section.footer),
            ("footer", "first", section.first_page_footer),
            ("footer", "even", section.even_page_footer),
        ):
            paragraph_texts = [logical_text(paragraph._p) for paragraph in container.paragraphs]
            texts.extend(paragraph_texts)
            elements.append(container._element)
            items.append(
                HeaderFooterIR(
                    part=part_name,
                    section_index=index,
                    locator=f"s{index + 1}.{part_name}.{variant}",
                    variant=variant,
                    linked_to_previous=bool(container.is_linked_to_previous),
                    paragraph_texts=paragraph_texts,
                )
            )
    return items, texts, elements


def _parse_relationships(document: DocumentObject) -> list[RelationshipIR]:
    package = document.part.package
    containers = [("/", package)]
    containers.extend((str(part.partname), part) for part in package.parts)
    relationships: list[RelationshipIR] = []
    for source_part, container in containers:
        for relationship in container.rels.values():
            relationships.append(
                RelationshipIR(
                    source_part=source_part,
                    relationship_id=relationship.rId,
                    relationship_type=relationship.reltype,
                    target=relationship.target_ref,
                    external=relationship.is_external,
                )
            )
    return sorted(
        relationships,
        key=lambda item: (item.source_part, item.relationship_id, item.relationship_type),
    )


def _bookmark_names(elements: list[Any]) -> list[str]:
    names = [
        name
        for element in elements
        for node in element.iter(qn("w:bookmarkStart"))
        if (name := node.get(qn("w:name"))) is not None
    ]
    return sorted(names)


def _node_id(prefix: str, part: str, index: int, text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{part}-{index:06d}-{digest}"


def _contains_tag(element: Any, tags: set[str]) -> bool:
    return any(node.tag in tags for node in element.iter())


def _numbering_info(paragraph: Paragraph) -> NumberingInfo | None:
    ppr = paragraph._p.pPr
    num_pr: Any | None = ppr.numPr if ppr is not None else None
    style = paragraph.style
    while num_pr is None and style is not None:
        style_ppr = style.element.pPr
        if style_ppr is not None and style_ppr.numPr is not None:
            num_pr = style_ppr.numPr
            break
        style = style.base_style
    if num_pr is None:
        return None
    num_id = num_pr.numId
    level = num_pr.ilvl
    return NumberingInfo(
        num_id=int(num_id.val) if num_id is not None and num_id.val is not None else None,
        level=int(level.val) if level is not None and level.val is not None else None,
    )


def _run_format(run: Any) -> RunFormatSnapshot:
    rpr = run._r.rPr
    fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
    color = run.font.color.rgb
    underline = run.font.underline
    return RunFormatSnapshot(
        ascii_font=fonts.get(qn("w:ascii")) if fonts is not None else None,
        high_ansi_font=fonts.get(qn("w:hAnsi")) if fonts is not None else None,
        east_asia_font=fonts.get(qn("w:eastAsia")) if fonts is not None else None,
        complex_script_font=fonts.get(qn("w:cs")) if fonts is not None else None,
        size_pt=round(run.font.size.pt, 3) if run.font.size is not None else None,
        bold=run.bold,
        italic=run.italic,
        underline=bool(underline) if underline is not None else None,
        color_hex=str(color) if color is not None else None,
    )


def _paragraph_format(paragraph: Paragraph) -> ParagraphFormatSnapshot:
    fmt = paragraph.paragraph_format
    line_spacing = fmt.line_spacing
    line_value: float | None
    if line_spacing is None:
        line_value = None
    elif hasattr(line_spacing, "pt"):
        line_value = round(float(line_spacing.pt), 3)
    else:
        line_value = float(line_spacing)
    return ParagraphFormatSnapshot(
        alignment=_enum_name(paragraph.alignment),
        line_spacing=line_value,
        line_spacing_rule=_enum_name(fmt.line_spacing_rule),
        space_before_pt=_points(fmt.space_before),
        space_after_pt=_points(fmt.space_after),
        first_line_indent_pt=_points(fmt.first_line_indent),
        left_indent_pt=_points(fmt.left_indent),
        right_indent_pt=_points(fmt.right_indent),
        keep_with_next=fmt.keep_with_next,
        keep_lines_together=fmt.keep_together,
        page_break_before=fmt.page_break_before,
    )


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    return str(name).lower() if name else str(value).lower()


def _points(value: Any) -> float | None:
    return round(float(value.pt), 3) if value is not None else None


def _twips(value: Any) -> int | None:
    return int(value.twips) if value is not None else None
