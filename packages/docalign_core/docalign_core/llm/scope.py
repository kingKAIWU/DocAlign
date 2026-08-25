from __future__ import annotations

import re

from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    CHINESE_FONT_SIZES_PT,
    AutoLayoutSpec,
    DocumentFormattingSpec,
    FontSpec,
    ForcePolicy,
    FormattingBehavior,
    FormattingProperty,
    FormattingSpec,
    HeaderFooterFormattingSpec,
    PageFormattingSpec,
    ParagraphSpec,
    RoleFormattingSpec,
    TableFormattingSpec,
    VisualCleanupSpec,
)
from docalign_core.llm.base import DocumentSummary

_ROLE_PATTERNS: tuple[tuple[SemanticRole, str], ...] = (
    (SemanticRole.TITLE, r"主标题|文档标题|题名|document\s+title"),
    (SemanticRole.SUBTITLE, r"副标题|subtitle"),
    (SemanticRole.AUTHOR_INFO, r"作者信息|作者单位|author\s+info"),
    (SemanticRole.ABSTRACT_HEADING, r"摘要标题|abstract\s+heading"),
    (SemanticRole.ABSTRACT_BODY, r"摘要正文|abstract\s+body"),
    (SemanticRole.KEYWORDS, r"关键词|keywords?"),
    (SemanticRole.LIST_ITEM, r"列表项|项目符号|编号列表|list\s*items?"),
    (SemanticRole.HEADING_1, r"一级标题|一[级阶]标题|heading\s*1"),
    (SemanticRole.HEADING_2, r"二级标题|二[级阶]标题|heading\s*2"),
    (SemanticRole.HEADING_3, r"三级标题|三[级阶]标题|heading\s*3"),
    (SemanticRole.HEADING_4, r"四级标题|四[级阶]标题|heading\s*4"),
    (SemanticRole.BODY, r"正文|普通段落|body(?:\s+text)?"),
    (SemanticRole.BLOCKQUOTE, r"引用段落|块引用|blockquote|block\s+quote"),
    (SemanticRole.FIGURE_CAPTION, r"图题|图片标题|figure\s+caption"),
    (SemanticRole.TABLE_CAPTION, r"表题|表格标题|table\s+caption"),
    (SemanticRole.BIBLIOGRAPHY_HEADING, r"参考文献标题|bibliography\s+heading"),
    (SemanticRole.BIBLIOGRAPHY_ENTRY, r"参考文献条目|参考文献正文|bibliography\s+entr"),
    (SemanticRole.APPENDIX_HEADING, r"附录标题|appendix\s+heading"),
    (SemanticRole.APPENDIX_BODY, r"附录正文|appendix\s+body"),
    (SemanticRole.UNKNOWN, r"未知角色|未识别段落|unknown\s+(?:role|paragraph)"),
)
_ALL_TEXT = re.compile(
    r"全文|全篇|所有(?:文字|文本|段落)|全部(?:文字|文本|段落)"
    r"|whole\s+document|all\s+(?:text|paragraphs?)",
    re.I,
)
_GENERIC_HEADING = re.compile(r"(?<!主)(?<!副)(?<!文档)标题|headings?", re.I)
_FONT_SIZE_NAMES = "|".join(sorted(map(re.escape, CHINESE_FONT_SIZES_PT), key=len, reverse=True))


def scope_natural_language_spec(
    user_text: str,
    model_spec: FormattingSpec,
    document_summary: DocumentSummary | None = None,
) -> FormattingSpec:
    """Remove model-invented formatting outside the user's explicit request.

    The model is an interpreter, not an authority to expand scope. This local pass is deliberately
    conservative: an unscoped typography request applies to body text only, while page layout,
    headings, tables, figures, headers, footers, and page numbers require explicit wording.
    """

    global_scope = _ALL_TEXT.search(user_text) is not None
    selected_roles = _selected_roles(user_text, default_to_body=not global_scope)
    baseline: RoleFormattingSpec | None = None
    if global_scope:
        baseline_candidate = model_spec.baseline or model_spec.roles.get(SemanticRole.BODY)
        if baseline_candidate is None and model_spec.roles:
            baseline_candidate = next(iter(model_spec.roles.values()))
        if baseline_candidate is not None:
            baseline = _scope_role(user_text, baseline_candidate)
    roles: dict[SemanticRole, RoleFormattingSpec] = {}
    for role in selected_roles:
        candidate = model_spec.roles.get(role)
        if candidate is None:
            continue
        scoped = _scope_role(user_text, candidate)
        if scoped is not None:
            roles[role] = scoped

    document = _scope_document(user_text, model_spec)
    tables = _scope_table(user_text, model_spec)
    figures = model_spec.figures if _mentions(user_text, r"图片|图像|插图|figure|image") else None
    headers = (
        _scope_header_footer(user_text, model_spec.headers)
        if _mentions(user_text, r"页眉|header")
        else None
    )
    footers = (
        _scope_header_footer(user_text, model_spec.footers)
        if _mentions(user_text, r"页脚|footer")
        else None
    )
    page_numbers = (
        model_spec.page_numbers
        if _mentions(user_text, r"页码|page\s+numbers?|pagination")
        else None
    )
    visual_cleanup = _scope_visual_cleanup(user_text)
    auto_layout = _scope_auto_layout(user_text)

    assumptions = _scoped_assumptions(
        model_spec.source.assumptions,
        document=document is not None,
        baseline=baseline is not None,
        roles=set(roles),
        tables=tables is not None,
        figures=figures is not None,
        headers=headers is not None,
        footers=footers is not None,
        page_numbers=page_numbers is not None,
    )
    assumptions.append("本地作用域控制：未明确提及的页面、标题、列表布局、表格和图片格式保持原样。")
    if visual_cleanup is not None and visual_cleanup.text_color_hex is not None:
        assumptions.append("“所有颜色”按所有可见文字颜色解释；图片、形状、边框和线条保持原样。")
    if visual_cleanup is not None and visual_cleanup.remove_text_highlight:
        assumptions.append("“不需要背景”按清除文本高亮、字符/段落/单元格底纹及页面背景解释。")
    if auto_layout.enabled:
        assumptions.append(
            "自动排版仅拆分无字段、图片、超链接、书签、公式和编号的普通正文段落；受保护结构保持原样。"
        )
    source = model_spec.source.model_copy(update={"assumptions": list(dict.fromkeys(assumptions))})
    return FormattingSpec(
        document=document,
        baseline=baseline,
        roles=roles,
        tables=tables,
        figures=figures,
        headers=headers,
        footers=footers,
        page_numbers=page_numbers,
        visual_cleanup=visual_cleanup,
        auto_layout=auto_layout,
        behavior=FormattingBehavior(),
        source=source,
    )


def applied_capabilities(spec: FormattingSpec) -> list[str]:
    capabilities: list[str] = []
    if spec.document is not None:
        capabilities.append("page_layout")
    if spec.baseline is not None:
        capabilities.append("document_typography")
    if spec.roles:
        capabilities.append("role_typography")
    if spec.tables is not None:
        capabilities.append("table_formatting")
    if spec.figures is not None:
        capabilities.append("figure_formatting")
    if spec.headers is not None or spec.footers is not None:
        capabilities.append("header_footer_formatting")
    if spec.page_numbers is not None:
        capabilities.append("page_numbers")
    if spec.visual_cleanup is not None:
        if spec.visual_cleanup.text_color_hex is not None:
            capabilities.append("document_text_color")
        if any(
            (
                spec.visual_cleanup.remove_text_highlight,
                spec.visual_cleanup.remove_character_shading,
                spec.visual_cleanup.remove_paragraph_shading,
                spec.visual_cleanup.remove_table_cell_shading,
                spec.visual_cleanup.remove_page_background,
            )
        ):
            capabilities.append("document_background_cleanup")
    if spec.auto_layout.enabled:
        capabilities.append("auto_layout")
    return capabilities


def _scope_auto_layout(user_text: str) -> AutoLayoutSpec:
    enabled = _mentions(
        user_text,
        r"自动排版|智能排版|自动分段|正文分段|段落重排|识别.{0,8}(?:标题|层级)|auto(?:matic)?\s+layout|paragraph\s+segmentation",
    )
    return AutoLayoutSpec(enabled=enabled)


def _selected_roles(user_text: str, *, default_to_body: bool) -> set[SemanticRole]:
    roles = {role for role, pattern in _ROLE_PATTERNS if _mentions(user_text, pattern)}
    if _GENERIC_HEADING.search(user_text) and not any(
        role in roles
        for role in {
            SemanticRole.TITLE,
            SemanticRole.SUBTITLE,
            SemanticRole.HEADING_1,
            SemanticRole.HEADING_2,
            SemanticRole.HEADING_3,
            SemanticRole.HEADING_4,
        }
    ):
        roles.update(
            {
                SemanticRole.HEADING_1,
                SemanticRole.HEADING_2,
                SemanticRole.HEADING_3,
                SemanticRole.HEADING_4,
            }
        )
    if roles or not default_to_body:
        return roles
    return {SemanticRole.BODY}


def _scope_role(user_text: str, candidate: RoleFormattingSpec) -> RoleFormattingSpec | None:
    font, forced = _scope_font(user_text, candidate.font)
    paragraph, paragraph_forced = _scope_paragraph(user_text, candidate.paragraph)
    forced.update(paragraph_forced)
    style_name = (
        candidate.style_name if _mentions(user_text, r"样式名|段落样式|style\s+name") else None
    )
    if font is None and paragraph is None and style_name is None:
        return None
    return RoleFormattingSpec(
        font=font,
        paragraph=paragraph,
        style_name=style_name,
        force=ForcePolicy(properties=forced),
    )


def _scope_font(
    user_text: str, candidate: FontSpec | None
) -> tuple[FontSpec | None, set[FormattingProperty]]:
    if candidate is None:
        return None, set()
    generic_font = _mentions(user_text, r"字体|字型|font(?:\s+family)?")
    allowed = {
        "east_asia": generic_font
        or _mentions(user_text, r"中文|汉字|中日韩|宋体|黑体|楷体|仿宋|微软雅黑|思源|方正"),
        "ascii": generic_font
        or _mentions(user_text, r"英文|西文|拉丁|latin|Times\s+New\s+Roman|Arial|Calibri"),
        "high_ansi": generic_font
        or _mentions(user_text, r"英文|西文|拉丁|latin|Times\s+New\s+Roman|Arial|Calibri"),
        "complex_script": generic_font
        or _mentions(user_text, r"复杂文种|complex\s+script|阿拉伯|希伯来"),
        "size_pt": _mentions(
            user_text,
            rf"字号|字体大小|font\s+size|(?:{_FONT_SIZE_NAMES})|\d+(?:\.\d+)?\s*(?:pt|磅|号)",
        ),
        "bold": _mentions(user_text, r"加粗|粗体|不加粗|取消加粗|bold"),
        "italic": _mentions(user_text, r"斜体|不斜体|取消斜体|italic"),
        "underline": _mentions(user_text, r"下划线|无下划线|取消下划线|underline"),
        "color_hex": _mentions(user_text, r"字体颜色|文字颜色|font\s+colou?r|#[0-9a-fA-F]{6}"),
    }
    payload = {
        name: getattr(candidate, name)
        for name, is_allowed in allowed.items()
        if is_allowed and getattr(candidate, name) is not None
    }
    if not payload:
        return None, set()
    property_map = {
        "east_asia": FormattingProperty.FONT_EAST_ASIA,
        "ascii": FormattingProperty.FONT_ASCII,
        "high_ansi": FormattingProperty.FONT_HIGH_ANSI,
        "complex_script": FormattingProperty.FONT_COMPLEX_SCRIPT,
        "size_pt": FormattingProperty.FONT_SIZE,
        "bold": FormattingProperty.FONT_BOLD,
        "italic": FormattingProperty.FONT_ITALIC,
        "underline": FormattingProperty.FONT_UNDERLINE,
        "color_hex": FormattingProperty.FONT_COLOR,
    }
    return FontSpec.model_validate(payload), {property_map[name] for name in payload}


def _scope_paragraph(
    user_text: str, candidate: ParagraphSpec | None
) -> tuple[ParagraphSpec | None, set[FormattingProperty]]:
    if candidate is None:
        return None, set()
    allowed = {
        "alignment": _mentions(user_text, r"对齐|居中|居左|居右|两端|alignment|align|justify"),
        "line_spacing": _mentions(user_text, r"行距|行间距|line\s+spacing"),
        "space_before_pt": _mentions(user_text, r"段前|space\s+before"),
        "space_after_pt": _mentions(user_text, r"段后|space\s+after"),
        "first_line_indent_pt": _mentions(user_text, r"首行缩进|first[- ]line\s+indent"),
        "hanging_indent_pt": _mentions(user_text, r"悬挂缩进|hanging\s+indent"),
        "left_indent_pt": _mentions(user_text, r"左缩进|left\s+indent"),
        "right_indent_pt": _mentions(user_text, r"右缩进|right\s+indent"),
        "keep_with_next": _mentions(user_text, r"与下段同页|keep\s+with\s+next"),
        "keep_lines_together": _mentions(user_text, r"段中不分页|keep\s+lines\s+together"),
        "page_break_before": _mentions(user_text, r"段前分页|另起一页|page\s+break\s+before"),
        "widow_orphan_control": _mentions(user_text, r"孤行控制|widow|orphan"),
    }
    payload = {
        name: getattr(candidate, name)
        for name, is_allowed in allowed.items()
        if is_allowed and getattr(candidate, name) is not None
    }
    if not payload:
        return None, set()
    property_map = {
        "alignment": FormattingProperty.PARAGRAPH_ALIGNMENT,
        "line_spacing": FormattingProperty.PARAGRAPH_LINE_SPACING,
        "space_before_pt": FormattingProperty.PARAGRAPH_SPACE_BEFORE,
        "space_after_pt": FormattingProperty.PARAGRAPH_SPACE_AFTER,
        "first_line_indent_pt": FormattingProperty.PARAGRAPH_FIRST_LINE_INDENT,
        "left_indent_pt": FormattingProperty.PARAGRAPH_LEFT_INDENT,
        "right_indent_pt": FormattingProperty.PARAGRAPH_RIGHT_INDENT,
    }
    return ParagraphSpec.model_validate(payload), {
        property_map[name] for name in payload if name in property_map
    }


def _scope_document(user_text: str, model_spec: FormattingSpec) -> DocumentFormattingSpec | None:
    if model_spec.document is None:
        return None
    page = model_spec.document.page
    generic_margins = _mentions(user_text, r"页边距|page\s+margins?")
    allowed = {
        "size": _mentions(user_text, r"\bA4\b|\bLetter\b|纸张大小|页面大小|page\s+size"),
        "orientation": _mentions(user_text, r"横向|纵向|纸张方向|页面方向|landscape|portrait"),
        "margin_top_mm": generic_margins or _mentions(user_text, r"上(?:页)?边距|top\s+margin"),
        "margin_bottom_mm": generic_margins
        or _mentions(user_text, r"下(?:页)?边距|bottom\s+margin"),
        "margin_left_mm": generic_margins or _mentions(user_text, r"左(?:页)?边距|left\s+margin"),
        "margin_right_mm": generic_margins or _mentions(user_text, r"右(?:页)?边距|right\s+margin"),
        "header_distance_mm": _mentions(user_text, r"页眉距离|header\s+distance"),
        "footer_distance_mm": _mentions(user_text, r"页脚距离|footer\s+distance"),
    }
    payload = {
        name: getattr(page, name)
        for name, is_allowed in allowed.items()
        if is_allowed and getattr(page, name) is not None
    }
    if not payload:
        return None
    return DocumentFormattingSpec(page=PageFormattingSpec.model_validate(payload))


def _scope_table(user_text: str, model_spec: FormattingSpec) -> TableFormattingSpec | None:
    if model_spec.tables is None or not _mentions(user_text, r"表格|单元格|table(?:s|\s+cell)?"):
        return None
    candidate = model_spec.tables
    font, _ = _scope_font(user_text, candidate.font)
    paragraph, _ = _scope_paragraph(user_text, candidate.paragraph)
    alignment = (
        candidate.alignment
        if _mentions(user_text, r"表格.{0,8}(?:对齐|居中|居左|居右)|table.{0,12}align")
        else None
    )
    width_explicit = _mentions(user_text, r"表格.{0,8}宽|单元格.{0,8}宽|table.{0,12}width|适应页面")
    vertical = (
        candidate.cell_vertical_alignment
        if _mentions(user_text, r"单元格.{0,8}垂直|cell.{0,12}vertical")
        else None
    )
    if (
        font is None
        and paragraph is None
        and alignment is None
        and not width_explicit
        and vertical is None
    ):
        return None
    return TableFormattingSpec(
        alignment=alignment,
        width_policy=candidate.width_policy if width_explicit else "preserve",
        fixed_width_mm=candidate.fixed_width_mm if width_explicit else None,
        cell_vertical_alignment=vertical,
        font=font,
        paragraph=paragraph,
    )


def _scope_header_footer(
    user_text: str, candidate: HeaderFooterFormattingSpec | None
) -> HeaderFooterFormattingSpec | None:
    if candidate is None:
        return None
    font, _ = _scope_font(user_text, candidate.font)
    paragraph, _ = _scope_paragraph(user_text, candidate.paragraph)
    if font is None and paragraph is None:
        return None
    return HeaderFooterFormattingSpec(font=font, paragraph=paragraph)


def _scope_visual_cleanup(user_text: str) -> VisualCleanupSpec | None:
    all_text_black = _mentions(
        user_text,
        r"(?:所有|全部|全文|全篇).{0,10}(?:文字|字体|文本)?颜色.{0,10}(?:黑色|黑)"
        r"|(?:文字|字体|文本)?颜色.{0,10}(?:统一|全部|所有|改为|设为).{0,10}(?:黑色|黑)"
        r"|all\s+(?:text\s+)?colou?rs?.{0,10}black",
    )
    remove_background = _mentions(
        user_text,
        r"不(?:需要|要|保留).{0,8}(?:背景|底色|底纹|高亮)"
        r"|(?:去掉|去除|清除|移除|取消).{0,8}(?:背景|底色|底纹|高亮)"
        r"|无背景|no\s+(?:background|highlight|shading)",
    )
    if not all_text_black and not remove_background:
        return None
    return VisualCleanupSpec(
        text_color_hex="000000" if all_text_black else None,
        remove_text_highlight=remove_background,
        remove_character_shading=remove_background,
        remove_paragraph_shading=remove_background,
        remove_table_cell_shading=remove_background,
        remove_page_background=remove_background,
    )


def _scoped_assumptions(
    assumptions: list[str],
    *,
    document: bool,
    baseline: bool,
    roles: set[SemanticRole],
    tables: bool,
    figures: bool,
    headers: bool,
    footers: bool,
    page_numbers: bool,
) -> list[str]:
    kept: list[str] = []
    heading_roles = {
        SemanticRole.TITLE,
        SemanticRole.SUBTITLE,
        SemanticRole.HEADING_1,
        SemanticRole.HEADING_2,
        SemanticRole.HEADING_3,
        SemanticRole.HEADING_4,
    }
    for assumption in assumptions:
        lowered = assumption.lower()
        if not document and re.search(
            r"margin|page size|orientation|\ba4\b|\bletter\b|页边距|纸张|页面方向",
            lowered,
        ):
            continue
        if not roles.intersection(heading_roles) and re.search(r"heading|title|标题", lowered):
            continue
        if (
            not baseline
            and SemanticRole.UNKNOWN not in roles
            and re.search(r"unknown|未知|未识别", lowered)
        ):
            continue
        if not tables and re.search(r"table|表格|单元格", lowered):
            continue
        if not figures and re.search(r"figure|image|图片|图像", lowered):
            continue
        if not headers and re.search(r"header|页眉", lowered):
            continue
        if not footers and re.search(r"footer|页脚", lowered):
            continue
        if not page_numbers and re.search(r"page number|页码", lowered):
            continue
        kept.append(assumption)
    return kept


def _mentions(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None
