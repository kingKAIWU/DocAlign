from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from zipfile import ZipFile

from docx.oxml.ns import qn
from lxml import etree

from docalign_core.docx.safety import sha256_file
from docalign_core.docx.xml_utils import logical_text
from docalign_core.domain.manifest import (
    FormatManifest,
    FormatManifestSummary,
    ManifestRequirement,
)

ManifestCategory = Literal["style", "section", "table", "numbering", "header_footer"]


def extract_format_manifest(
    path: Path,
    *,
    document_id: str,
    source_filename: str | None = None,
) -> FormatManifest:
    """Extract an evidence-backed, deterministic format manifest from a DOCX.

    The manifest deliberately separates automatically comparable style/section
    facts from example-based table and header/footer evidence.  It is an audit
    artifact, not an instruction to blindly copy every source property.
    """

    requirements: list[ManifestRequirement] = []
    warnings: list[str] = []

    def add(
        category: ManifestCategory,
        target: str,
        target_label: str | None,
        property_path: str,
        expected: object,
        source_part: str,
        evidence: str,
        *,
        confidence: float,
        auto_applicable: bool,
    ) -> None:
        requirements.append(
            ManifestRequirement(
                requirement_id=f"R{len(requirements) + 1:04d}",
                category=category,
                target=target,
                target_label=target_label,
                property_path=property_path,
                expected=expected,
                source_part=source_part,
                evidence=_evidence(evidence),
                confidence=confidence,
                auto_applicable=auto_applicable,
            )
        )

    with ZipFile(path) as package:
        styles = _read_xml(package, "word/styles.xml")
        if styles is None:
            warnings.append("word/styles.xml is missing; style requirements were not extracted.")
        else:
            for style in styles.findall(qn("w:style")):
                if style.get(qn("w:type")) != "paragraph":
                    continue
                style_id = style.get(qn("w:styleId")) or "unknown"
                name_node = style.find(qn("w:name"))
                style_name = name_node.get(qn("w:val")) if name_node is not None else style_id
                target = f"style:{style_id}"
                evidence = f"paragraph style {style_name} ({style_id})"
                for key, value in {
                    **_paragraph_properties(style.find(qn("w:pPr"))),
                    **_run_properties(style.find(qn("w:rPr"))),
                }.items():
                    add(
                        "style",
                        target,
                        style_name,
                        key,
                        value,
                        "word/styles.xml",
                        evidence,
                        confidence=1.0,
                        auto_applicable=True,
                    )

        document = _read_xml(package, "word/document.xml")
        if document is None:
            warnings.append("word/document.xml is missing; layout examples were not extracted.")
        else:
            sections = document.findall(f".//{qn('w:sectPr')}")
            for section_index, section in enumerate(sections, start=1):
                for key, value in _section_properties(section).items():
                    add(
                        "section",
                        f"s{section_index}",
                        f"section {section_index}",
                        key,
                        value,
                        "word/document.xml",
                        f"section {section_index}",
                        confidence=1.0,
                        auto_applicable=True,
                    )
            for table_index, table in enumerate(
                document.findall(f".//{qn('w:body')}/{qn('w:tbl')}"), start=1
            ):
                table_properties = table.find(qn("w:tblPr"))
                evidence = logical_text(table)[:160] or f"table {table_index}"
                for key, value in _table_properties(table_properties).items():
                    add(
                        "table",
                        f"t{table_index}",
                        f"table {table_index}",
                        key,
                        value,
                        "word/document.xml",
                        evidence,
                        confidence=0.75,
                        auto_applicable=False,
                    )

        numbering = _read_xml(package, "word/numbering.xml")
        if numbering is not None:
            for level_index, level in enumerate(numbering.findall(f".//{qn('w:lvl')}"), start=1):
                number_format = level.find(qn("w:numFmt"))
                level_text = level.find(qn("w:lvlText"))
                for key, node in (
                    ("numbering.format", number_format),
                    ("numbering.text", level_text),
                ):
                    if node is None or node.get(qn("w:val")) is None:
                        continue
                    value = node.get(qn("w:val"))
                    add(
                        "numbering",
                        f"numbering-level:{level_index}",
                        f"numbering level {level_index}",
                        key,
                        value,
                        "word/numbering.xml",
                        (level_text.get(qn("w:val")) if level_text is not None else "")
                        or f"numbering level {level_index}",
                        confidence=0.8,
                        auto_applicable=False,
                    )

        header_footer_parts = sorted(
            name
            for name in package.namelist()
            if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
        )
        for part_name in header_footer_parts:
            root = _read_xml(package, part_name)
            if root is None:
                continue
            for paragraph_index, paragraph in enumerate(root.findall(f".//{qn('w:p')}"), start=1):
                text = logical_text(paragraph)
                properties = _paragraph_properties(paragraph.find(qn("w:pPr")))
                for key, value in properties.items():
                    add(
                        "header_footer",
                        f"{part_name}:p{paragraph_index}",
                        f"{part_name} paragraph {paragraph_index}",
                        key,
                        value,
                        part_name,
                        text or f"paragraph {paragraph_index}",
                        confidence=0.7,
                        auto_applicable=False,
                    )

    category_counts = Counter(item.category for item in requirements)
    return FormatManifest(
        document_id=document_id,
        source_filename=source_filename or path.name,
        source_sha256=sha256_file(path),
        requirements=requirements,
        summary=FormatManifestSummary(
            requirement_count=len(requirements),
            by_category=dict(sorted(category_counts.items())),
            auto_applicable_count=sum(item.auto_applicable for item in requirements),
        ),
        warnings=warnings,
    )


def _read_xml(package: ZipFile, name: str) -> Any | None:
    try:
        return etree.fromstring(package.read(name))
    except KeyError:
        return None


def _run_properties(run_properties: Any | None) -> dict[str, object]:
    if run_properties is None:
        return {}
    fonts = run_properties.find(qn("w:rFonts"))
    properties: dict[str, object | None] = {
        "font.ascii": fonts.get(qn("w:ascii")) if fonts is not None else None,
        "font.east_asia": fonts.get(qn("w:eastAsia")) if fonts is not None else None,
        "font.high_ansi": fonts.get(qn("w:hAnsi")) if fonts is not None else None,
        "font.complex_script": fonts.get(qn("w:cs")) if fonts is not None else None,
        "font.size_pt": _half_points(run_properties.find(qn("w:sz"))),
        "font.bold": _optional_boolean(run_properties.find(qn("w:b"))),
        "font.italic": _optional_boolean(run_properties.find(qn("w:i"))),
        "font.underline": _attribute(run_properties.find(qn("w:u")), "w:val"),
        "font.color_hex": _attribute(run_properties.find(qn("w:color")), "w:val"),
        "font.character_spacing_twentieth_pt": _integer_attribute(
            run_properties.find(qn("w:spacing")), "w:val"
        ),
        "font.character_scale_percent": _integer_attribute(run_properties.find(qn("w:w")), "w:val"),
    }
    return {key: value for key, value in properties.items() if value is not None}


def _paragraph_properties(paragraph_properties: Any | None) -> dict[str, object]:
    if paragraph_properties is None:
        return {}
    spacing = paragraph_properties.find(qn("w:spacing"))
    indentation = paragraph_properties.find(qn("w:ind"))
    properties: dict[str, object | None] = {
        "paragraph.alignment": _attribute(paragraph_properties.find(qn("w:jc")), "w:val"),
        "paragraph.space_before_twips": _integer_attribute(spacing, "w:before"),
        "paragraph.space_after_twips": _integer_attribute(spacing, "w:after"),
        "paragraph.line_twips": _integer_attribute(spacing, "w:line"),
        "paragraph.line_rule": _attribute(spacing, "w:lineRule"),
        "paragraph.first_line_twips": _integer_attribute(indentation, "w:firstLine"),
        "paragraph.first_line_chars_hundredth": _integer_attribute(indentation, "w:firstLineChars"),
        "paragraph.left_indent_twips": _integer_attribute(indentation, "w:left"),
        "paragraph.right_indent_twips": _integer_attribute(indentation, "w:right"),
        "paragraph.keep_with_next": _optional_boolean(paragraph_properties.find(qn("w:keepNext"))),
        "paragraph.keep_lines_together": _optional_boolean(
            paragraph_properties.find(qn("w:keepLines"))
        ),
        "paragraph.page_break_before": _optional_boolean(
            paragraph_properties.find(qn("w:pageBreakBefore"))
        ),
    }
    return {key: value for key, value in properties.items() if value is not None}


def _section_properties(section: Any) -> dict[str, object]:
    page_size = section.find(qn("w:pgSz"))
    margins = section.find(qn("w:pgMar"))
    properties: dict[str, object | None] = {
        "page.width_twips": _integer_attribute(page_size, "w:w"),
        "page.height_twips": _integer_attribute(page_size, "w:h"),
        "page.orientation": _attribute(page_size, "w:orient"),
        "page.margin_top_twips": _integer_attribute(margins, "w:top"),
        "page.margin_bottom_twips": _integer_attribute(margins, "w:bottom"),
        "page.margin_left_twips": _integer_attribute(margins, "w:left"),
        "page.margin_right_twips": _integer_attribute(margins, "w:right"),
        "page.header_distance_twips": _integer_attribute(margins, "w:header"),
        "page.footer_distance_twips": _integer_attribute(margins, "w:footer"),
    }
    return {key: value for key, value in properties.items() if value is not None}


def _table_properties(table_properties: Any | None) -> dict[str, object]:
    if table_properties is None:
        return {}
    width = table_properties.find(qn("w:tblW"))
    properties: dict[str, object | None] = {
        "table.style": _attribute(table_properties.find(qn("w:tblStyle")), "w:val"),
        "table.alignment": _attribute(table_properties.find(qn("w:jc")), "w:val"),
        "table.width": _integer_attribute(width, "w:w"),
        "table.width_type": _attribute(width, "w:type"),
        "table.layout": _attribute(table_properties.find(qn("w:tblLayout")), "w:type"),
    }
    return {key: value for key, value in properties.items() if value is not None}


def _attribute(element: Any | None, name: str) -> str | None:
    return element.get(qn(name)) if element is not None else None


def _integer_attribute(element: Any | None, name: str) -> int | None:
    value = _attribute(element, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _half_points(element: Any | None) -> float | None:
    value = _integer_attribute(element, "w:val")
    return value / 2 if value is not None else None


def _optional_boolean(element: Any | None) -> bool | None:
    if element is None:
        return None
    return (element.get(qn("w:val")) or "1").lower() not in {"0", "false", "off"}


def _evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:300]
