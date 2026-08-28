from __future__ import annotations

import re
from collections import Counter
from statistics import median

from docalign_core.domain.document_ir import (
    AnalysisResult,
    AnalysisSummary,
    DocumentIR,
    DocumentWarning,
    ParagraphIR,
    RoleOverride,
)
from docalign_core.domain.enums import AnalysisMode, DocumentKind, RoleSource, SemanticRole

STYLE_ROLE_MAP: dict[str, SemanticRole] = {
    "title": SemanticRole.TITLE,
    "标题": SemanticRole.TITLE,
    "subtitle": SemanticRole.SUBTITLE,
    "副标题": SemanticRole.SUBTITLE,
    "heading1": SemanticRole.HEADING_1,
    "标题1": SemanticRole.HEADING_1,
    "一级标题": SemanticRole.HEADING_1,
    "heading2": SemanticRole.HEADING_2,
    "标题2": SemanticRole.HEADING_2,
    "二级标题": SemanticRole.HEADING_2,
    "heading3": SemanticRole.HEADING_3,
    "标题3": SemanticRole.HEADING_3,
    "三级标题": SemanticRole.HEADING_3,
    "heading4": SemanticRole.HEADING_4,
    "标题4": SemanticRole.HEADING_4,
    "四级标题": SemanticRole.HEADING_4,
    "caption": SemanticRole.FIGURE_CAPTION,
    "图题": SemanticRole.FIGURE_CAPTION,
    "表题": SemanticRole.TABLE_CAPTION,
    "quote": SemanticRole.BLOCKQUOTE,
    "引用": SemanticRole.BLOCKQUOTE,
    "listbullet": SemanticRole.LIST_ITEM,
    "listnumber": SemanticRole.LIST_ITEM,
    "listparagraph": SemanticRole.LIST_ITEM,
    "列表段落": SemanticRole.LIST_ITEM,
    "项目符号": SemanticRole.LIST_ITEM,
    "编号": SemanticRole.LIST_ITEM,
    "dabody": SemanticRole.BODY,
    "datitle": SemanticRole.TITLE,
    "dasubtitle": SemanticRole.SUBTITLE,
    "daauthorinfo": SemanticRole.AUTHOR_INFO,
    "daabstractheading": SemanticRole.ABSTRACT_HEADING,
    "daabstract": SemanticRole.ABSTRACT_BODY,
    "dakeywords": SemanticRole.KEYWORDS,
    "daheading1": SemanticRole.HEADING_1,
    "daheading2": SemanticRole.HEADING_2,
    "daheading3": SemanticRole.HEADING_3,
    "daheading4": SemanticRole.HEADING_4,
    "dablockquote": SemanticRole.BLOCKQUOTE,
    "dalistitem": SemanticRole.LIST_ITEM,
    "dafigurecaption": SemanticRole.FIGURE_CAPTION,
    "datablecaption": SemanticRole.TABLE_CAPTION,
    "dabibliographyheading": SemanticRole.BIBLIOGRAPHY_HEADING,
    "dabibliography": SemanticRole.BIBLIOGRAPHY_ENTRY,
    "daappendixheading": SemanticRole.APPENDIX_HEADING,
    "daappendix": SemanticRole.APPENDIX_BODY,
}

FIGURE_CAPTION = re.compile(
    r"^\s*(?:图\s*[0-9一二三四五六七八九十]+(?:[-.][0-9]+)*|Figure\s+\d+|Fig\.\s*\d+)",
    re.IGNORECASE,
)
TABLE_CAPTION = re.compile(
    r"^\s*(?:表\s*[0-9一二三四五六七八九十]+(?:[-.][0-9]+)*|Table\s+\d+)",
    re.IGNORECASE,
)
HEADING_PATTERNS: list[tuple[re.Pattern[str], SemanticRole]] = [
    (
        re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+条(?:\s|　|$)"),
        SemanticRole.HEADING_2,
    ),
    (
        re.compile(
            r"^\s*(?:第[一二三四五六七八九十百0-9]+章|Chapter\s+[0-9IVX]+)(?:\s|　|$)",
            re.IGNORECASE,
        ),
        SemanticRole.HEADING_1,
    ),
    (re.compile(r"^\s*\d+\.\d+\.\d+\.\d+(?:\s|　|$)"), SemanticRole.HEADING_4),
    (re.compile(r"^\s*\d+\.\d+\.\d+(?:\s|　|$)"), SemanticRole.HEADING_3),
    (re.compile(r"^\s*\d+\.\d+(?:\s|　|$)"), SemanticRole.HEADING_2),
    (re.compile(r"^\s*\d+[、.\s]\s*\S+"), SemanticRole.HEADING_1),
    (re.compile(r"^\s*[一二三四五六七八九十]+、\s*\S+"), SemanticRole.HEADING_1),
    (re.compile(r"^\s*[（(][一二三四五六七八九十]+[)）]\s*\S+"), SemanticRole.HEADING_2),
    (re.compile(r"^\s*[（(]\d+[)）]\s*\S+"), SemanticRole.HEADING_2),
]
BIB_ENTRY = re.compile(r"^\s*(?:\[\d+]|\d+[.)、])\s*")
LEXICAL_HEADINGS: dict[str, SemanticRole] = {
    label: SemanticRole.HEADING_1
    for label in {
        "引言",
        "前言",
        "绪论",
        "研究方法",
        "研究结果",
        "结果与讨论",
        "讨论",
        "结论",
        "结语",
        "总结",
        "致谢",
        "执行摘要",
        "核心结论",
        "风险与行动",
        "会议目标",
        "讨论要点",
        "行动项",
        "个人简介",
        "核心能力",
        "工作经历",
        "项目经历",
        "教育经历",
        "专业技能",
        "技能",
        "证书",
        "获奖经历",
        "常见问题",
        "预算执行说明",
        "附则",
        "签署页",
        "introduction",
        "methods",
        "methodology",
        "results",
        "discussion",
        "conclusion",
        "conclusions",
        "acknowledgements",
    }
}


def analyze_document(document_ir: DocumentIR) -> AnalysisResult:
    analyzed = document_ir.model_copy(deep=True)
    paragraphs = [block for block in analyzed.blocks if isinstance(block, ParagraphIR)]
    previous_nonempty: ParagraphIR | None = None
    in_abstract = False
    in_bibliography = False
    title_seen = False
    nonempty_position = 0
    warnings = list(analyzed.warnings)
    uniform_format_pollution = _has_uniform_short_format_pollution(paragraphs)
    inferred_format_headings = _infer_format_heading_roles(paragraphs)

    for position, paragraph in enumerate(paragraphs):
        if paragraph.is_empty:
            _assign(paragraph, SemanticRole.UNKNOWN, 1.0, RoleSource.DETERMINISTIC, "empty")
            continue
        nonempty_position += 1
        text = paragraph.text.strip()
        normalized = _normalize_label(text)
        style_role = STYLE_ROLE_MAP.get(_normalize_style(paragraph.current_style_name))
        if style_role is not None:
            _assign(paragraph, style_role, 0.99, RoleSource.EXISTING_STYLE, "known-style")
        elif normalized in {"摘要", "中文摘要", "abstract"}:
            _assign(
                paragraph,
                SemanticRole.ABSTRACT_HEADING,
                0.99,
                RoleSource.DETERMINISTIC,
                "abstract-heading",
            )
            in_abstract = True
            in_bibliography = False
        elif normalized in {"参考文献", "references", "bibliography"}:
            _assign(
                paragraph,
                SemanticRole.BIBLIOGRAPHY_HEADING,
                0.99,
                RoleSource.DETERMINISTIC,
                "bibliography-heading",
            )
            in_bibliography = True
            in_abstract = False
        elif normalized in {"附录", "appendix"} or text.startswith("附录"):
            _assign(
                paragraph,
                SemanticRole.APPENDIX_HEADING,
                0.96,
                RoleSource.DETERMINISTIC,
                "appendix-heading",
            )
            in_abstract = False
            in_bibliography = False
        elif re.match(r"^\s*(关键词|关键字|keywords?)\s*[:：]", text, re.IGNORECASE):
            _assign(
                paragraph,
                SemanticRole.KEYWORDS,
                0.98,
                RoleSource.DETERMINISTIC,
                "keywords-prefix",
            )
            in_abstract = False
        elif FIGURE_CAPTION.match(text):
            confidence = 0.95 if previous_nonempty and previous_nonempty.contains_drawing else 0.86
            _assign(
                paragraph,
                SemanticRole.FIGURE_CAPTION,
                confidence,
                RoleSource.DETERMINISTIC,
                "figure-caption-pattern",
            )
        elif TABLE_CAPTION.match(text):
            _assign(
                paragraph,
                SemanticRole.TABLE_CAPTION,
                0.9,
                RoleSource.DETERMINISTIC,
                "table-caption-pattern",
            )
        elif (lexical_heading := _lexical_heading(text)) is not None:
            _assign(
                paragraph,
                lexical_heading,
                0.91,
                RoleSource.DETERMINISTIC,
                "section-label",
            )
            in_abstract = False
            in_bibliography = False
        elif paragraph.numbering is not None:
            _assign(
                paragraph,
                SemanticRole.LIST_ITEM,
                0.88,
                RoleSource.DETERMINISTIC,
                "word-numbering",
            )
        elif (heading := _numbered_heading(text)) is not None:
            _assign(paragraph, heading, 0.94, RoleSource.DETERMINISTIC, "numbering-pattern")
            in_abstract = False
        elif in_bibliography and (BIB_ENTRY.match(text) or len(text) > 20):
            _assign(
                paragraph,
                SemanticRole.BIBLIOGRAPHY_ENTRY,
                0.87,
                RoleSource.DETERMINISTIC,
                "bibliography-region",
            )
        elif in_abstract:
            _assign(
                paragraph,
                SemanticRole.ABSTRACT_BODY,
                0.86,
                RoleSource.DETERMINISTIC,
                "abstract-region",
            )
        elif not title_seen and nonempty_position <= 5 and _looks_like_title(paragraph):
            _assign(
                paragraph,
                SemanticRole.TITLE,
                0.82,
                RoleSource.DETERMINISTIC,
                "front-centered-short-text",
            )
            title_seen = True
        elif title_seen and nonempty_position <= 8 and _looks_like_author_info(paragraph):
            _assign(
                paragraph,
                SemanticRole.AUTHOR_INFO,
                0.76,
                RoleSource.DETERMINISTIC,
                "front-matter-after-title",
            )
        elif paragraph.node_id in inferred_format_headings:
            _assign(
                paragraph,
                inferred_format_headings[paragraph.node_id],
                0.8,
                RoleSource.DETERMINISTIC,
                "relative-heading-format",
            )
            in_abstract = False
            in_bibliography = False
        elif not uniform_format_pollution and _heading_format_candidate(paragraph):
            _assign(
                paragraph,
                SemanticRole.UNKNOWN,
                0.58,
                RoleSource.DETERMINISTIC,
                "heading-like-formatting",
            )
        else:
            _assign(paragraph, SemanticRole.BODY, 0.75, RoleSource.FALLBACK, "body-fallback")

        assigned_role = paragraph.detected_role
        if assigned_role == SemanticRole.TITLE:
            title_seen = True
        if assigned_role == SemanticRole.ABSTRACT_HEADING:
            in_abstract = True
            in_bibliography = False
        elif assigned_role == SemanticRole.BIBLIOGRAPHY_HEADING:
            in_bibliography = True
            in_abstract = False
        elif assigned_role == SemanticRole.KEYWORDS:
            in_abstract = False
        elif assigned_role in {
            SemanticRole.HEADING_1,
            SemanticRole.HEADING_2,
            SemanticRole.HEADING_3,
            SemanticRole.HEADING_4,
            SemanticRole.APPENDIX_HEADING,
        }:
            in_abstract = False
            in_bibliography = False

        if (
            paragraph.numbering is None
            and assigned_role == SemanticRole.BODY
            and _has_numbered_heading_prefix(text)
            and _numbered_heading(text) is None
        ):
            warnings.append(
                DocumentWarning(
                    code="POSSIBLE_MIXED_HEADING_BODY",
                    node_id=paragraph.node_id,
                    message=(
                        "The paragraph begins like a numbered heading but also contains body "
                        "text; it was preserved as one body paragraph."
                    ),
                    details={"position": position},
                )
            )

        if paragraph.role_confidence < 0.75:
            warnings.append(
                DocumentWarning(
                    code="LOW_CONFIDENCE_ROLE",
                    node_id=paragraph.node_id,
                    message=f"Paragraph role confidence is {paragraph.role_confidence:.2f}.",
                    details={"candidate_role": paragraph.detected_role.value, "position": position},
                )
            )
        previous_nonempty = paragraph

    analyzed.warnings = warnings
    document_kind, kind_confidence = _infer_document_kind(analyzed)
    summary = build_analysis_summary(
        analyzed,
        document_kind=document_kind,
        document_kind_confidence=kind_confidence,
    )
    return AnalysisResult(document_ir=analyzed, summary=summary, warnings=warnings)


def build_analysis_summary(
    document_ir: DocumentIR,
    *,
    analysis_mode: AnalysisMode = AnalysisMode.DETERMINISTIC,
    document_kind: DocumentKind | None = None,
    document_kind_confidence: float = 0.0,
    model_reviewed_paragraphs: int = 0,
    model_provider: str | None = None,
    model_name: str | None = None,
) -> AnalysisSummary:
    role_counts = Counter(
        block.detected_role.value for block in document_ir.blocks if isinstance(block, ParagraphIR)
    )
    reviewable_unknown_count = sum(
        1
        for block in document_ir.blocks
        if isinstance(block, ParagraphIR)
        and block.detected_role == SemanticRole.UNKNOWN
        and (not block.is_empty or block.contains_drawing)
    )
    return AnalysisSummary(
        paragraph_count=document_ir.metadata.paragraph_count,
        table_count=document_ir.metadata.table_count,
        image_count=document_ir.metadata.image_count,
        unknown_count=reviewable_unknown_count,
        role_counts=dict(sorted(role_counts.items())),
        existing_styles=document_ir.metadata.existing_styles,
        analysis_mode=analysis_mode,
        document_kind=document_kind,
        document_kind_confidence=document_kind_confidence,
        model_reviewed_paragraphs=model_reviewed_paragraphs,
        model_provider=model_provider,
        model_name=model_name,
    )


def apply_role_overrides(document_ir: DocumentIR, overrides: list[RoleOverride]) -> DocumentIR:
    updated = document_ir.model_copy(deep=True)
    lookup = {override.node_id: override.role for override in overrides}
    for block in updated.blocks:
        if isinstance(block, ParagraphIR) and block.node_id in lookup:
            block.detected_role = lookup[block.node_id]
            block.role_confidence = 1.0
            block.role_source = RoleSource.USER_OVERRIDE
            block.role_evidence = ["user-override"]
    return updated


def _assign(
    paragraph: ParagraphIR,
    role: SemanticRole,
    confidence: float,
    source: RoleSource,
    evidence: str,
) -> None:
    paragraph.detected_role = role
    paragraph.role_confidence = confidence
    paragraph.role_source = source
    paragraph.role_evidence = [evidence]


def _normalize_style(value: str | None) -> str:
    return re.sub(r"[\s_\-]", "", (value or "").strip().lower())


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s:：]", "", value).lower()


def _numbered_heading(text: str) -> SemanticRole | None:
    stripped = text.strip()
    if len(stripped) > 80 or (
        len(stripped) > 40 and stripped.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
    ):
        return None
    for pattern, role in HEADING_PATTERNS:
        if pattern.match(stripped):
            return role
    return None


def _has_numbered_heading_prefix(text: str) -> bool:
    return any(pattern.match(text.strip()) is not None for pattern, _ in HEADING_PATTERNS)


def _looks_like_title(paragraph: ParagraphIR) -> bool:
    if len(paragraph.text.strip()) > 80:
        return False
    centered = paragraph.formatting.alignment in {"center", "center (1)"}
    bold_ratio = _bold_ratio(paragraph)
    large = any((run.formatting.size_pt or 0) >= 15 for run in paragraph.runs)
    return centered and (bold_ratio >= 0.6 or large)


def _looks_like_author_info(paragraph: ParagraphIR) -> bool:
    text = paragraph.text.strip()
    centered = paragraph.formatting.alignment in {"center", "center (1)"}
    return centered and len(text) <= 80 and not _numbered_heading(text)


def _heading_format_candidate(paragraph: ParagraphIR) -> bool:
    text = paragraph.text.strip()
    return len(text) <= 60 and _bold_ratio(paragraph) >= 0.8


def _lexical_heading(text: str) -> SemanticRole | None:
    return LEXICAL_HEADINGS.get(_normalize_label(text))


def _infer_format_heading_roles(paragraphs: list[ParagraphIR]) -> dict[str, SemanticRole]:
    if _has_uniform_short_format_pollution(paragraphs):
        return {}
    measured_body_sizes = [
        size
        for paragraph in paragraphs
        if len(paragraph.text.strip()) >= 80
        if (size := _paragraph_size(paragraph)) is not None
    ]
    all_sizes = [
        size for paragraph in paragraphs if (size := _paragraph_size(paragraph)) is not None
    ]
    body_size = float(median(measured_body_sizes or all_sizes or [12.0]))
    candidates: list[tuple[ParagraphIR, float | None]] = []
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if (
            paragraph.is_empty
            or len(text) > 60
            or text.endswith(("。", "！", "？", "；", ".", "!", "?", ";"))
        ):
            continue
        size = _paragraph_size(paragraph)
        strong_size = size is not None and size >= body_size + 1
        structural_spacing = paragraph.formatting.keep_with_next is True or (
            (paragraph.formatting.space_before_pt or 0)
            > (paragraph.formatting.space_after_pt or 0) + 3
        )
        if _bold_ratio(paragraph) >= 0.7 or strong_size or structural_spacing:
            candidates.append((paragraph, size))

    heading_sizes = sorted(
        {size for _, size in candidates if size is not None and size >= body_size},
        reverse=True,
    )[:4]
    roles = [
        SemanticRole.HEADING_1,
        SemanticRole.HEADING_2,
        SemanticRole.HEADING_3,
        SemanticRole.HEADING_4,
    ]
    inferred: dict[str, SemanticRole] = {}
    for paragraph, size in candidates:
        centered = paragraph.formatting.alignment in {"center", "center (1)"}
        if centered and (size or body_size) >= body_size + 1:
            role = SemanticRole.HEADING_1
        elif size is not None and size in heading_sizes:
            role = roles[min(heading_sizes.index(size), len(roles) - 1)]
        elif paragraph.formatting.keep_with_next is True:
            role = SemanticRole.HEADING_2
        else:
            role = SemanticRole.HEADING_3
        inferred[paragraph.node_id] = role
    return inferred


def _has_uniform_short_format_pollution(paragraphs: list[ParagraphIR]) -> bool:
    eligible = [
        paragraph
        for paragraph in paragraphs
        if not paragraph.is_empty and len(paragraph.text.strip()) <= 60
    ]
    signatures = Counter(
        (
            round(_paragraph_size(paragraph) or 0, 1),
            _bold_ratio(paragraph) >= 0.7,
        )
        for paragraph in eligible
    )
    if eligible and signatures:
        bold_counts = [count for (_, bold), count in signatures.items() if bold]
        dominant_count = max(bold_counts, default=0)
        if dominant_count >= 4 and dominant_count / len(eligible) >= 0.4:
            return True
    return False


def _infer_document_kind(document_ir: DocumentIR) -> tuple[DocumentKind, float]:
    paragraphs = [
        block.text.strip()
        for block in document_ir.blocks
        if isinstance(block, ParagraphIR) and block.text.strip()
    ]
    joined = "\n".join(paragraphs).lower()
    labels = {_normalize_label(text) for text in paragraphs}
    article_count = sum(
        bool(re.match(r"^第[一二三四五六七八九十百千万0-9]+条(?:\s|　|$)", text))
        for text in paragraphs
    )
    parties_present = any(word in joined for word in ("甲方", "乙方"))
    if article_count >= 2 or ("合同" in joined and parties_present):
        return DocumentKind.CONTRACT, 0.96
    resume_labels = labels & {
        "个人简介",
        "核心能力",
        "工作经历",
        "项目经历",
        "教育经历",
        "专业技能",
        "技能",
    }
    if "简历" in joined[:120] or len(resume_labels) >= 2:
        return DocumentKind.RESUME, 0.94
    if "会议纪要" in joined[:160] or len(labels & {"会议目标", "讨论要点", "行动项"}) >= 2:
        return DocumentKind.MEETING_MINUTES, 0.95
    if any(word in joined[:160] for word in ("通知", "公告", "请示", "批复")):
        return DocumentKind.GOVERNMENT_DOCUMENT, 0.88
    if any(word in joined[:160] for word in ("预算执行", "财务报告", "财务报表")):
        return DocumentKind.FINANCIAL_REPORT, 0.9
    if "摘要" in labels and ("关键词" in joined or "参考文献" in labels):
        return DocumentKind.ACADEMIC_PAPER, 0.92
    if any(word in joined[:160] for word in ("操作手册", "使用手册", "培训手册")):
        return DocumentKind.MANUAL, 0.91
    if any(word in joined[:160] for word in ("报告", "分析")):
        return DocumentKind.REPORT, 0.78
    if any(word in joined[:160] for word in ("方案", "建议书")):
        return DocumentKind.PROPOSAL, 0.8
    return DocumentKind.OTHER, 0.55


def _paragraph_size(paragraph: ParagraphIR) -> float | None:
    sizes = [
        run.formatting.size_pt
        for run in paragraph.runs
        if run.text.strip() and run.formatting.size_pt is not None
    ]
    return max(sizes) if sizes else None


def _bold_ratio(paragraph: ParagraphIR) -> float:
    weighted = [(len(run.text), run.formatting.bold is True) for run in paragraph.runs if run.text]
    total = sum(length for length, _ in weighted)
    if total == 0:
        return 0.0
    return sum(length for length, bold in weighted if bold) / total
