from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from docalign_core.analysis.classifier import analyze_document
from docalign_core.docx.manifest import extract_format_manifest
from docalign_core.docx.parser import parse_docx
from docalign_core.domain.document_ir import ParagraphIR
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    Alignment,
    AutoLayoutSpec,
    DocumentFormattingSpec,
    FontSpec,
    FormattingSpec,
    LineSpacingMode,
    LineSpacingSpec,
    Orientation,
    PageFormattingSpec,
    PageSize,
    ParagraphSpec,
    RoleFormattingSpec,
    SpecSource,
    SpecSourceType,
)
from docalign_core.domain.manifest import ManifestRequirement
from docalign_core.domain.template_candidate import (
    TemplateCandidateSummary,
    TemplateRoleMapping,
    TemplateRuleCandidate,
)

_STYLE_ROLE_MAP: dict[str, SemanticRole] = {
    "normal": SemanticRole.BODY,
    "body": SemanticRole.BODY,
    "bodytext": SemanticRole.BODY,
    "正文": SemanticRole.BODY,
    "普通正文": SemanticRole.BODY,
    "dabody": SemanticRole.BODY,
    "title": SemanticRole.TITLE,
    "标题": SemanticRole.TITLE,
    "datitle": SemanticRole.TITLE,
    "subtitle": SemanticRole.SUBTITLE,
    "副标题": SemanticRole.SUBTITLE,
    "dasubtitle": SemanticRole.SUBTITLE,
    "heading1": SemanticRole.HEADING_1,
    "标题1": SemanticRole.HEADING_1,
    "一级标题": SemanticRole.HEADING_1,
    "daheading1": SemanticRole.HEADING_1,
    "heading2": SemanticRole.HEADING_2,
    "标题2": SemanticRole.HEADING_2,
    "二级标题": SemanticRole.HEADING_2,
    "daheading2": SemanticRole.HEADING_2,
    "heading3": SemanticRole.HEADING_3,
    "标题3": SemanticRole.HEADING_3,
    "三级标题": SemanticRole.HEADING_3,
    "daheading3": SemanticRole.HEADING_3,
    "heading4": SemanticRole.HEADING_4,
    "标题4": SemanticRole.HEADING_4,
    "四级标题": SemanticRole.HEADING_4,
    "daheading4": SemanticRole.HEADING_4,
    "blockquote": SemanticRole.BLOCKQUOTE,
    "quote": SemanticRole.BLOCKQUOTE,
    "引用": SemanticRole.BLOCKQUOTE,
    "dablockquote": SemanticRole.BLOCKQUOTE,
    "listparagraph": SemanticRole.LIST_ITEM,
    "列表段落": SemanticRole.LIST_ITEM,
    "dalistitem": SemanticRole.LIST_ITEM,
}

_ALIGNMENTS: dict[str, Alignment] = {
    "left": Alignment.LEFT,
    "start": Alignment.LEFT,
    "center": Alignment.CENTER,
    "right": Alignment.RIGHT,
    "end": Alignment.RIGHT,
    "both": Alignment.JUSTIFY,
    "distribute": Alignment.JUSTIFY,
    "justify": Alignment.JUSTIFY,
}


def compile_template_rule_candidate(path: Path, *, source_filename: str) -> TemplateRuleCandidate:
    """Compile only high-confidence, deterministic facts from a known-good DOCX.

    This deliberately produces a candidate for human confirmation. Example-based
    tables, numbering, headers and footers are reported but never copied blindly.
    """

    manifest = extract_format_manifest(
        path,
        document_id="template_reference",
        source_filename=source_filename,
    )
    analysis = analyze_document(parse_docx(path, document_id="template_reference"))
    paragraphs = [
        block
        for block in analysis.document_ir.blocks
        if isinstance(block, ParagraphIR) and not block.is_empty
    ]
    usage: dict[str, list[ParagraphIR]] = defaultdict(list)
    for paragraph in paragraphs:
        if paragraph.current_style_name:
            usage[_normalize_style(paragraph.current_style_name)].append(paragraph)

    style_requirements: dict[str, list[ManifestRequirement]] = defaultdict(list)
    for requirement in manifest.requirements:
        if requirement.category == "style":
            style_requirements[requirement.target].append(requirement)

    ambiguities: list[str] = []
    candidates: dict[SemanticRole, list[tuple[int, int, float, str, list[ManifestRequirement]]]] = (
        defaultdict(list)
    )
    for requirements in style_requirements.values():
        label = requirements[0].target_label or requirements[0].target.removeprefix("style:")
        normalized = _normalize_style(label)
        matching = usage.get(normalized, [])
        if not matching:
            continue
        explicit_role = _STYLE_ROLE_MAP.get(normalized)
        inferred_role, inferred_confidence = _infer_custom_style_role(matching)
        role = explicit_role or inferred_role
        if role is None:
            ambiguities.append(f"样式“{label}”已在正文中使用，但无法稳定映射到文档角色。")
            continue
        confidence = 1.0 if explicit_role else inferred_confidence
        candidates[role].append(
            (
                1 if explicit_role else 0,
                len(matching),
                confidence,
                label,
                requirements,
            )
        )

    applied_ids: set[str] = set()
    role_specs: dict[SemanticRole, RoleFormattingSpec] = {}
    role_mappings: list[TemplateRoleMapping] = []
    for role in sorted(candidates, key=lambda item: item.value):
        ranked = sorted(
            candidates[role],
            key=lambda item: (item[0], item[1], item[2]),
            reverse=True,
        )
        selected = ranked[0]
        if len(ranked) > 1:
            alternatives = "、".join(item[3] for item in ranked[1:])
            ambiguities.append(
                f"角色“{role.value}”存在多套已使用样式；"
                f"候选采用“{selected[3]}”，未采用 {alternatives}。"
            )
        role_spec, included, requirement_ids = _compile_role_spec(selected[4])
        if role_spec is None:
            ambiguities.append(f"样式“{selected[3]}”没有可安全转换的显式格式属性。")
            continue
        role_specs[role] = role_spec
        applied_ids.update(requirement_ids)
        role_mappings.append(
            TemplateRoleMapping(
                role=role,
                source_style_name=selected[3],
                paragraph_count=selected[1],
                confidence=selected[2],
                included_properties=included,
            )
        )

    page_spec, page_ids, page_ambiguities = _compile_page_spec(manifest.requirements)
    applied_ids.update(page_ids)
    ambiguities.extend(page_ambiguities)

    unsupported: list[str] = []
    category_counts = manifest.summary.by_category
    if category_counts.get("table", 0):
        unsupported.append("表格属性来自个别示例，当前只展示证据，不自动推广到所有表格。")
    if category_counts.get("numbering", 0):
        unsupported.append("编号定义可能包含多级列表依赖，当前不自动复制。")
    if category_counts.get("header_footer", 0):
        unsupported.append("页眉页脚可能包含字段和分节关系，当前不自动复制。")

    assumptions = [
        "仅采用统一页面参数和能稳定映射到语义角色的已使用段落样式。",
        "候选规则默认不拆分正文；应用前必须由用户确认。",
        "未显式写入样式 XML 的主题字体和继承属性不会被猜测。",
    ]
    spec = FormattingSpec(
        document=DocumentFormattingSpec(page=page_spec) if page_spec is not None else None,
        roles=role_specs,
        auto_layout=AutoLayoutSpec(enabled=False),
        source=SpecSource(
            type=SpecSourceType.TEMPLATE,
            reference_filename=source_filename,
            reference_sha256=manifest.source_sha256,
            compiler_version="template-candidate.v1",
            assumptions=assumptions,
        ),
    )
    auto_count = sum(
        1
        for requirement in manifest.requirements
        if requirement.auto_applicable
        and (
            requirement.category == "section"
            or (
                requirement.category == "style"
                and _normalize_style(requirement.target_label or "") in usage
            )
        )
    )
    applied_count = len(applied_ids)
    coverage = round(applied_count / auto_count * 100, 1) if auto_count else 0.0
    warnings = [
        *manifest.warnings,
        "这是待确认的候选规则，不代表完整复制 Word 模板。",
        "参考文档只用于本次本地提取，服务不会把它作为待处理文档保存。",
    ]
    return TemplateRuleCandidate(
        source_filename=source_filename,
        source_sha256=manifest.source_sha256,
        safe_to_apply=bool(applied_ids),
        spec=spec,
        summary=TemplateCandidateSummary(
            source_requirement_count=manifest.summary.requirement_count,
            auto_applicable_requirement_count=auto_count,
            applied_requirement_count=applied_count,
            mapped_role_count=len(role_mappings),
            coverage_percent=coverage,
        ),
        role_mappings=role_mappings,
        applied_requirement_ids=sorted(applied_ids),
        ambiguities=_deduplicate(ambiguities),
        unsupported_features=unsupported,
        warnings=warnings,
    )


def _infer_custom_style_role(paragraphs: list[ParagraphIR]) -> tuple[SemanticRole | None, float]:
    confident = [
        paragraph
        for paragraph in paragraphs
        if paragraph.detected_role != SemanticRole.UNKNOWN and paragraph.role_confidence >= 0.85
    ]
    if not confident:
        return None, 0.0
    counts = Counter(paragraph.detected_role for paragraph in confident)
    role, count = counts.most_common(1)[0]
    if count / len(paragraphs) < 0.8:
        return None, 0.0
    matching = [item.role_confidence for item in confident if item.detected_role == role]
    return role, round(sum(matching) / len(matching), 3)


def _compile_role_spec(
    requirements: list[ManifestRequirement],
) -> tuple[RoleFormattingSpec | None, list[str], set[str]]:
    by_path = {item.property_path: item for item in requirements if item.auto_applicable}
    font_payload: dict[str, object] = {}
    paragraph_payload: dict[str, object] = {}
    included: list[str] = []
    applied: set[str] = set()

    font_fields = {
        "font.east_asia": "east_asia",
        "font.ascii": "ascii",
        "font.high_ansi": "high_ansi",
        "font.complex_script": "complex_script",
        "font.size_pt": "size_pt",
        "font.bold": "bold",
        "font.italic": "italic",
    }
    for property_path, field_name in font_fields.items():
        requirement = by_path.get(property_path)
        if requirement is None:
            continue
        font_payload[field_name] = requirement.expected
        included.append(property_path)
        applied.add(requirement.requirement_id)

    underline = by_path.get("font.underline")
    if underline is not None and isinstance(underline.expected, str):
        font_payload["underline"] = underline.expected.lower() not in {"none", "0", "false"}
        included.append(underline.property_path)
        applied.add(underline.requirement_id)

    color = by_path.get("font.color_hex")
    if color is not None and isinstance(color.expected, str) and re.fullmatch(
        r"#?[0-9A-Fa-f]{6}", color.expected
    ):
        font_payload["color_hex"] = color.expected
        included.append(color.property_path)
        applied.add(color.requirement_id)

    alignment = by_path.get("paragraph.alignment")
    if alignment is not None and isinstance(alignment.expected, str):
        mapped_alignment = _ALIGNMENTS.get(alignment.expected.lower())
        if mapped_alignment is not None:
            paragraph_payload["alignment"] = mapped_alignment
            included.append(alignment.property_path)
            applied.add(alignment.requirement_id)

    paragraph_fields = {
        "paragraph.space_before_twips": "space_before_pt",
        "paragraph.space_after_twips": "space_after_pt",
        "paragraph.first_line_twips": "first_line_indent_pt",
        "paragraph.left_indent_twips": "left_indent_pt",
        "paragraph.right_indent_twips": "right_indent_pt",
    }
    for property_path, field_name in paragraph_fields.items():
        requirement = by_path.get(property_path)
        value = _number(requirement.expected) if requirement is not None else None
        if requirement is None or value is None:
            continue
        paragraph_payload[field_name] = value / 20
        included.append(property_path)
        applied.add(requirement.requirement_id)

    boolean_fields = {
        "paragraph.keep_with_next": "keep_with_next",
        "paragraph.keep_lines_together": "keep_lines_together",
        "paragraph.page_break_before": "page_break_before",
    }
    for property_path, field_name in boolean_fields.items():
        requirement = by_path.get(property_path)
        if requirement is None or not isinstance(requirement.expected, bool):
            continue
        paragraph_payload[field_name] = requirement.expected
        included.append(property_path)
        applied.add(requirement.requirement_id)

    line = by_path.get("paragraph.line_twips")
    line_rule = by_path.get("paragraph.line_rule")
    if line is not None and line_rule is not None:
        line_value = _number(line.expected)
        rule_value = line_rule.expected if isinstance(line_rule.expected, str) else ""
        line_spacing = _line_spacing(line_value, rule_value)
        if line_spacing is not None:
            paragraph_payload["line_spacing"] = line_spacing
            included.extend([line.property_path, line_rule.property_path])
            applied.update([line.requirement_id, line_rule.requirement_id])

    font = FontSpec.model_validate(font_payload) if font_payload else None
    paragraph = ParagraphSpec.model_validate(paragraph_payload) if paragraph_payload else None
    if font is None and paragraph is None:
        return None, [], set()
    return RoleFormattingSpec(font=font, paragraph=paragraph), sorted(included), applied


def _compile_page_spec(
    requirements: list[ManifestRequirement],
) -> tuple[PageFormattingSpec | None, set[str], list[str]]:
    by_path: dict[str, list[ManifestRequirement]] = defaultdict(list)
    for requirement in requirements:
        if requirement.category == "section" and requirement.auto_applicable:
            by_path[requirement.property_path].append(requirement)
    payload: dict[str, object] = {}
    applied: set[str] = set()
    ambiguities: list[str] = []

    def unanimous(property_path: str) -> tuple[object | None, list[ManifestRequirement]]:
        items = by_path.get(property_path, [])
        if not items:
            return None, []
        first = items[0].expected
        if any(item.expected != first for item in items[1:]):
            ambiguities.append(f"参考文档不同分节的 {property_path} 不一致，未自动采用。")
            return None, []
        return first, items

    width, width_items = unanimous("page.width_twips")
    height, height_items = unanimous("page.height_twips")
    width_number = _number(width)
    height_number = _number(height)
    if width_number is not None and height_number is not None:
        page_size = _page_size(width_number, height_number)
        if page_size is not None:
            payload["size"] = page_size
            applied.update(item.requirement_id for item in [*width_items, *height_items])

    orientation, orientation_items = unanimous("page.orientation")
    if isinstance(orientation, str) and orientation.lower() == "landscape":
        payload["orientation"] = Orientation.LANDSCAPE
        applied.update(item.requirement_id for item in orientation_items)
    elif width_number is not None and height_number is not None:
        payload["orientation"] = (
            Orientation.LANDSCAPE if width_number > height_number else Orientation.PORTRAIT
        )
        applied.update(item.requirement_id for item in [*width_items, *height_items])

    page_fields = {
        "page.margin_top_twips": "margin_top_mm",
        "page.margin_bottom_twips": "margin_bottom_mm",
        "page.margin_left_twips": "margin_left_mm",
        "page.margin_right_twips": "margin_right_mm",
        "page.header_distance_twips": "header_distance_mm",
        "page.footer_distance_twips": "footer_distance_mm",
    }
    for property_path, field_name in page_fields.items():
        value, items = unanimous(property_path)
        numeric = _number(value)
        if numeric is None:
            continue
        payload[field_name] = round(numeric / 1440 * 25.4, 2)
        applied.update(item.requirement_id for item in items)

    return (
        PageFormattingSpec.model_validate(payload) if payload else None,
        applied,
        ambiguities,
    )


def _line_spacing(value: float | None, rule: str) -> LineSpacingSpec | None:
    if value is None or value <= 0:
        return None
    normalized = rule.lower()
    if normalized == "auto":
        return LineSpacingSpec(mode=LineSpacingMode.MULTIPLE, value=round(value / 240, 3))
    if normalized == "exact":
        return LineSpacingSpec(mode=LineSpacingMode.EXACT, value=round(value / 20, 3))
    if normalized in {"atleast", "at_least"}:
        return LineSpacingSpec(mode=LineSpacingMode.AT_LEAST, value=round(value / 20, 3))
    return None


def _page_size(width: float, height: float) -> PageSize | None:
    short, long = sorted((width, height))
    if abs(short - 11906) <= 90 and abs(long - 16838) <= 90:
        return PageSize.A4
    if abs(short - 12240) <= 90 and abs(long - 15840) <= 90:
        return PageSize.LETTER
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _normalize_style(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value).lower()


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
