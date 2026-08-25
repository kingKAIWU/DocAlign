from __future__ import annotations

from typing import Any

from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from docx.text.run import Run
from lxml import etree

from docalign_core.domain.formatting_spec import (
    Alignment,
    FontSpec,
    FormattingProperty,
    LineSpacingMode,
    ParagraphSpec,
    RoleFormattingSpec,
)

ALIGNMENT_MAP = {
    Alignment.LEFT: WD_ALIGN_PARAGRAPH.LEFT,
    Alignment.CENTER: WD_ALIGN_PARAGRAPH.CENTER,
    Alignment.RIGHT: WD_ALIGN_PARAGRAPH.RIGHT,
    Alignment.JUSTIFY: WD_ALIGN_PARAGRAPH.JUSTIFY,
}
TABLE_ALIGNMENT_MAP = {
    Alignment.LEFT: WD_TABLE_ALIGNMENT.LEFT,
    Alignment.CENTER: WD_TABLE_ALIGNMENT.CENTER,
    Alignment.RIGHT: WD_TABLE_ALIGNMENT.RIGHT,
}
CELL_ALIGNMENT_MAP = {
    "top": WD_CELL_VERTICAL_ALIGNMENT.TOP,
    "center": WD_CELL_VERTICAL_ALIGNMENT.CENTER,
    "bottom": WD_CELL_VERTICAL_ALIGNMENT.BOTTOM,
}

PORTABLE_FONT_ALIASES = {
    "宋体": "Songti SC",
    "黑体": "Heiti SC",
    "仿宋": "FangSong",
    "楷体": "Kaiti SC",
}


def ensure_portable_font_aliases(document: Any, font_names: set[str]) -> None:
    """Add cross-platform aliases without changing the Word-facing font names.

    Windows Word continues to see fonts such as ``宋体`` while renderers on macOS can
    resolve the bundled equivalent (for example ``Songti SC``).  ``python-docx`` does
    not add custom east-Asian fonts to ``fontTable.xml`` automatically.
    """

    aliases = {
        name: PORTABLE_FONT_ALIASES[name] for name in font_names if name in PORTABLE_FONT_ALIASES
    }
    if not aliases:
        return
    try:
        part = document.part.part_related_by(RT.FONT_TABLE)
    except KeyError:
        return
    root = etree.fromstring(part.blob)
    for name, alternate in aliases.items():
        font = next(
            (
                candidate
                for candidate in root.findall(qn("w:font"))
                if candidate.get(qn("w:name")) == name
            ),
            None,
        )
        if font is None:
            font = OxmlElement("w:font")
            font.set(qn("w:name"), name)
            root.append(font)
        alt_name = font.find(qn("w:altName"))
        if alt_name is None:
            alt_name = OxmlElement("w:altName")
            font.insert(0, alt_name)
        alt_name.set(qn("w:val"), alternate)
    part._blob = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def ensure_role_style(document: Any, style_name: str, role_spec: RoleFormattingSpec) -> Any:
    try:
        style = document.styles[style_name]
    except KeyError:
        style = document.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = document.styles["Normal"]
    if role_spec.font is not None:
        set_style_font(style, role_spec.font)
    if role_spec.paragraph is not None:
        apply_paragraph_spec(style.paragraph_format, role_spec.paragraph)
        _set_widow_control(style.element, role_spec.paragraph.widow_orphan_control)
    return style


def set_style_font(style: Any, spec: FontSpec) -> None:
    if spec.ascii:
        style.font.name = spec.ascii
    if spec.size_pt is not None:
        style.font.size = Pt(spec.size_pt)
    if spec.bold is not None:
        style.font.bold = spec.bold
    if spec.italic is not None:
        style.font.italic = spec.italic
    if spec.underline is not None:
        style.font.underline = spec.underline
    if spec.color_hex is not None:
        style.font.color.rgb = RGBColor.from_string(spec.color_hex)
    rpr = style.element.get_or_add_rPr()
    _set_rfonts(rpr, spec)


def set_run_font(run: Run, spec: FontSpec, forced: set[FormattingProperty]) -> None:
    rpr = run._r.get_or_add_rPr()
    _set_rfonts(rpr, spec)
    if spec.size_pt is not None:
        run.font.size = Pt(spec.size_pt)
    if spec.bold is not None and FormattingProperty.FONT_BOLD in forced:
        run.bold = spec.bold
    if spec.italic is not None and FormattingProperty.FONT_ITALIC in forced:
        run.italic = spec.italic
    if spec.underline is not None and FormattingProperty.FONT_UNDERLINE in forced:
        run.underline = spec.underline
    if spec.color_hex is not None and FormattingProperty.FONT_COLOR in forced:
        run.font.color.rgb = RGBColor.from_string(spec.color_hex)


def iter_all_runs(paragraph: Any) -> list[Run]:
    return [Run(element, paragraph) for element in paragraph._p.xpath(".//w:r")]


def run_is_protected(run: Run) -> bool:
    protected = {
        qn("w:fldChar"),
        qn("w:instrText"),
        qn("m:oMath"),
        qn("m:oMathPara"),
        qn("w:drawing"),
        qn("w:pict"),
    }
    return any(node.tag in protected for node in run._r.iter())


def clear_covered_paragraph_format(paragraph: Any, spec: ParagraphSpec) -> None:
    fmt = paragraph.paragraph_format
    if spec.alignment is not None:
        paragraph.alignment = None
    if spec.line_spacing is not None:
        fmt.line_spacing = None
        fmt.line_spacing_rule = None
    if spec.space_before_pt is not None:
        fmt.space_before = None
    if spec.space_after_pt is not None:
        fmt.space_after = None
    if spec.first_line_indent_pt is not None or spec.hanging_indent_pt is not None:
        fmt.first_line_indent = None
    if spec.left_indent_pt is not None:
        fmt.left_indent = None
    if spec.right_indent_pt is not None:
        fmt.right_indent = None
    if spec.keep_with_next is not None:
        fmt.keep_with_next = None
    if spec.keep_lines_together is not None:
        fmt.keep_together = None
    if spec.page_break_before is not None:
        fmt.page_break_before = None
    if spec.widow_orphan_control is not None:
        ppr = paragraph._p.get_or_add_pPr()
        for element in ppr.findall(qn("w:widowControl")):
            ppr.remove(element)


def apply_direct_paragraph_spec(paragraph: Any, spec: ParagraphSpec) -> None:
    """Used for tables and headers where a dedicated role style may not exist."""

    apply_paragraph_spec(paragraph.paragraph_format, spec, paragraph=paragraph)


def apply_paragraph_spec(
    format_proxy: Any, spec: ParagraphSpec, paragraph: Any | None = None
) -> None:
    if spec.alignment is not None:
        if paragraph is not None:
            paragraph.alignment = ALIGNMENT_MAP[spec.alignment]
        else:
            format_proxy.alignment = ALIGNMENT_MAP[spec.alignment]
    if spec.line_spacing is not None:
        spacing = spec.line_spacing
        if spacing.mode == LineSpacingMode.SINGLE:
            format_proxy.line_spacing_rule = WD_LINE_SPACING.SINGLE
            format_proxy.line_spacing = 1.0
        elif spacing.mode == LineSpacingMode.MULTIPLE:
            format_proxy.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
            format_proxy.line_spacing = spacing.value
        elif spacing.mode == LineSpacingMode.EXACT:
            format_proxy.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            format_proxy.line_spacing = Pt(spacing.value or 0)
        elif spacing.mode == LineSpacingMode.AT_LEAST:
            format_proxy.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
            format_proxy.line_spacing = Pt(spacing.value or 0)
    if spec.space_before_pt is not None:
        format_proxy.space_before = Pt(spec.space_before_pt)
    if spec.space_after_pt is not None:
        format_proxy.space_after = Pt(spec.space_after_pt)
    if spec.first_line_indent_pt is not None:
        format_proxy.first_line_indent = Pt(spec.first_line_indent_pt)
    elif spec.hanging_indent_pt is not None:
        format_proxy.first_line_indent = Pt(-spec.hanging_indent_pt)
    if spec.left_indent_pt is not None:
        format_proxy.left_indent = Pt(spec.left_indent_pt)
    if spec.right_indent_pt is not None:
        format_proxy.right_indent = Pt(spec.right_indent_pt)
    if spec.keep_with_next is not None:
        format_proxy.keep_with_next = spec.keep_with_next
    if spec.keep_lines_together is not None:
        format_proxy.keep_together = spec.keep_lines_together
    if spec.page_break_before is not None:
        format_proxy.page_break_before = spec.page_break_before
    if paragraph is not None:
        _set_widow_control(paragraph._p, spec.widow_orphan_control)


def set_table_width(
    table: Any,
    width_twips: int,
    *,
    column_weights: list[float] | None = None,
    min_column_width_twips: int = 0,
) -> None:
    """Set a fixed table width and scale its grid/cell geometry to match."""

    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(width_twips))
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    grid = table._tbl.tblGrid
    grid_columns = list(grid.findall(qn("w:gridCol")))
    existing_widths = [int(column.get(qn("w:w"), "0")) for column in grid_columns]
    if not existing_widths or any(value <= 0 for value in existing_widths):
        column_count = max(len(table.columns), 1)
        existing_widths = [1] * column_count
        for column in grid_columns:
            grid.remove(column)
        grid_columns = []
        for _ in range(column_count):
            column = OxmlElement("w:gridCol")
            grid.append(column)
            grid_columns.append(column)

    weights = (
        column_weights if column_weights and len(column_weights) == len(existing_widths) else None
    )
    if weights is None:
        weights = [float(value) for value in existing_widths]
    minimum = min(min_column_width_twips, width_twips // max(len(weights), 1))
    remaining_width = width_twips - minimum * len(weights)
    total_weight = sum(max(value, 0.01) for value in weights)
    scaled_widths = [
        minimum + max(1, round(remaining_width * max(value, 0.01) / total_weight))
        for value in weights
    ]
    scaled_widths[-1] += width_twips - sum(scaled_widths)
    if scaled_widths[-1] <= 0:
        column_count = len(scaled_widths)
        scaled_widths = [width_twips // column_count] * column_count
        scaled_widths[-1] += width_twips - sum(scaled_widths)

    for column, column_width in zip(grid_columns, scaled_widths, strict=True):
        column.set(qn("w:w"), str(column_width))

    for row in table._tbl.findall(qn("w:tr")):
        column_index = 0
        for cell in row.findall(qn("w:tc")):
            cell_properties = cell.find(qn("w:tcPr"))
            if cell_properties is None:
                cell_properties = OxmlElement("w:tcPr")
                cell.insert(0, cell_properties)
            grid_span = cell_properties.find(qn("w:gridSpan"))
            span = int(grid_span.get(qn("w:val"), "1")) if grid_span is not None else 1
            cell_width = sum(scaled_widths[column_index : column_index + span])
            width = cell_properties.find(qn("w:tcW"))
            if width is None:
                width = OxmlElement("w:tcW")
                cell_properties.append(width)
            width.set(qn("w:type"), "dxa")
            width.set(qn("w:w"), str(cell_width))
            column_index += span
    table.autofit = False


def suggest_table_column_weights(table: Any) -> list[float]:
    """Favor narrative columns while keeping IDs, dates, and numeric columns compact."""

    column_count = max(len(table.columns), 1)
    lengths: list[list[int]] = [[] for _ in range(column_count)]
    for row in table.rows:
        for index, cell in enumerate(row.cells[:column_count]):
            text = "".join(cell.text.split())
            lengths[index].append(len(text))
    weights: list[float] = []
    for values in lengths:
        longest = max(values or [1])
        average = sum(values) / max(len(values), 1)
        representative = max(average, longest * 0.55)
        weights.append(min(4.0, max(1.0, representative**0.5)))
    return weights


def effective_table_font_size(
    configured_size_pt: float | None,
    column_count: int,
    *,
    adaptive: bool,
    minimum_size_pt: float,
) -> float | None:
    if configured_size_pt is None or not adaptive or column_count <= 6:
        return configured_size_pt
    return max(minimum_size_pt, configured_size_pt - 0.5 * (column_count - 6))


def set_table_grid_borders(table: Any, *, color: str = "B7B7B7", size: int = 4) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_repeat_table_header(row: Any) -> None:
    properties = row._tr.get_or_add_trPr()
    if properties.find(qn("w:tblHeader")) is None:
        properties.append(OxmlElement("w:tblHeader"))


def set_row_cant_split(row: Any) -> None:
    properties = row._tr.get_or_add_trPr()
    if properties.find(qn("w:cantSplit")) is None:
        properties.append(OxmlElement("w:cantSplit"))


def mm_to_twips(value: float) -> int:
    return int(Mm(value).twips)


def insert_page_field(paragraph: Any) -> bool:
    if any("PAGE" in (node.text or "").upper() for node in paragraph._p.iter(qn("w:instrText"))):
        return False
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, text, end])
    return True


def _set_rfonts(rpr: Any, spec: FontSpec) -> None:
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    values = {
        "ascii": spec.ascii,
        "hAnsi": spec.high_ansi or spec.ascii,
        "eastAsia": spec.east_asia,
        "cs": spec.complex_script or spec.high_ansi or spec.ascii,
    }
    for name, value in values.items():
        if value:
            fonts.set(qn(f"w:{name}"), value)
            theme_name = {
                "ascii": "asciiTheme",
                "hAnsi": "hAnsiTheme",
                "eastAsia": "eastAsiaTheme",
                "cs": "cstheme",
            }[name]
            theme_attr = qn(f"w:{theme_name}")
            if theme_attr in fonts.attrib:
                del fonts.attrib[theme_attr]
    if spec.east_asia:
        fonts.set(qn("w:hint"), "eastAsia")
        language = rpr.find(qn("w:lang"))
        if language is None:
            language = OxmlElement("w:lang")
            rpr.append(language)
        language.set(qn("w:val"), "zh-CN")
        language.set(qn("w:eastAsia"), "zh-CN")


def _set_widow_control(owner: Any, value: bool | None) -> None:
    if value is None:
        return
    ppr = owner if owner.tag == qn("w:pPr") else owner.get_or_add_pPr()
    element = ppr.find(qn("w:widowControl"))
    if element is None:
        element = OxmlElement("w:widowControl")
        ppr.append(element)
    element.set(qn("w:val"), "1" if value else "0")
