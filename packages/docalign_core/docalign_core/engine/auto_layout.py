from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from docalign_core.analysis.classifier import analyze_document
from docalign_core.docx.parser import iter_block_items, parse_docx
from docalign_core.domain.audit import PlanWarning
from docalign_core.domain.base import StrictModel
from docalign_core.domain.document_ir import DocumentIR, ParagraphIR
from docalign_core.domain.enums import RoleSource, SemanticRole
from docalign_core.domain.formatting_spec import AutoLayoutSpec

_BODY_ROLES = {
    SemanticRole.BODY,
    SemanticRole.ABSTRACT_BODY,
    SemanticRole.APPENDIX_BODY,
}
_SENTENCE_END = re.compile(r"[。！？!?；;]+[\"'”’」』）)]*")
_CLAUSE_END = re.compile(r"[，,、：:]+")
_COLLAPSED_MAJOR_HEADING = re.compile(r"[一二三四五六七八九十]{1,3}、")
_COLLAPSED_DECIMAL_HEADING = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,3})[ \u3000]+")
_OUTLINE_BOUNDARY_PUNCTUATION = frozenset("。！？!?；;")
_BODY_OPENERS = re.compile(
    "|".join(
        sorted(
            {
                "本研究",
                "研究表明",
                "实验结果",
                "相较于",
                "融合后的",
                "近年来",
                "在国外",
                "在国内",
                "随着",
                "本文",
                "本节",
                "原始",
                "图像",
                "频域",
                "空域",
                "低频",
                "高频",
                "未来",
                "目前",
                "当前",
                "二者",
                "经过",
                "通过",
                "针对",
                "为了",
            },
            key=len,
            reverse=True,
        )
    )
)
_HEADING_TITLE_SUFFIXES = (
    "背景",
    "意义",
    "现状",
    "安排",
    "特性",
    "原理",
    "规律",
    "思路",
    "机制",
    "设计",
    "分析",
    "策略",
    "总结",
    "展望",
    "概述",
    "方法",
    "方案",
    "框架",
    "流程",
    "模型",
    "实验",
    "应用",
    "内容",
    "目标",
    "结论",
    "讨论",
    "结果",
    "路线",
    "研究",
)
_RUN_CONTENT_TAGS = {
    qn("w:rPr"),
    qn("w:t"),
    qn("w:tab"),
    qn("w:br"),
    qn("w:cr"),
    qn("w:lastRenderedPageBreak"),
}


class AutoLayoutChange(StrictModel):
    source_node_id: str
    before_text: str
    after_texts: list[str]
    reason: str


@dataclass(slots=True)
class AutoLayoutResult:
    source_path: Path
    document_ir: DocumentIR
    changes: list[AutoLayoutChange]
    warnings: list[PlanWarning]


class AutoLayoutIntegrityError(RuntimeError):
    pass


def apply_auto_layout(
    source_path: Path,
    document_ir: DocumentIR,
    spec: AutoLayoutSpec,
    output_path: Path,
) -> AutoLayoutResult:
    """Split safe continuous body text into real Word paragraphs.

    The source is never overwritten. Paragraphs containing fields, drawings, hyperlinks,
    bookmarks, equations, content controls, numbering, section properties, or unknown inline
    XML are left untouched.
    """

    if not spec.enabled or not spec.split_body_paragraphs:
        return AutoLayoutResult(source_path, document_ir, [], [])

    document = Document(str(source_path))
    actual_blocks = list(iter_block_items(document))
    changes: list[AutoLayoutChange] = []
    warnings: list[PlanWarning] = []
    output_counts: dict[str, int] = {}
    recovered_roles: dict[str, list[SemanticRole]] = {}

    for block in reversed(document_ir.blocks):
        if not isinstance(block, ParagraphIR):
            continue
        if "\n" in block.text and (block.formatting.alignment or "").lower().startswith("right"):
            warnings.append(
                PlanWarning(
                    code="AUTO_LAYOUT_SIGNATURE_PRESERVED",
                    node_id=block.node_id,
                    message="A right-aligned multi-line signature block was preserved intact.",
                )
            )
            continue
        manual_structure = spec.split_on_manual_breaks and "\n" in block.text
        if block.detected_role not in _BODY_ROLES and not manual_structure:
            continue
        outline_plan = _collapsed_numbered_outline_plan(block.text, spec)
        spans = (
            [(start, end) for start, end, _ in outline_plan]
            if outline_plan is not None
            else _segment_spans(block.text, spec)
        )
        if len(spans) <= 1:
            continue
        if block.index >= len(actual_blocks) or actual_blocks[block.index][0] != "paragraph":
            warnings.append(
                PlanWarning(
                    code="AUTO_LAYOUT_STRUCTURE_MISMATCH",
                    node_id=block.node_id,
                    message="A paragraph selected for automatic layout could not be located.",
                )
            )
            continue
        paragraph = actual_blocks[block.index][1]
        if not _paragraph_is_safe_to_split(paragraph, block):
            warnings.append(
                PlanWarning(
                    code="AUTO_LAYOUT_PROTECTED_PARAGRAPH_SKIPPED",
                    node_id=block.node_id,
                    message=(
                        "A paragraph needed segmentation but contained numbering or protected "
                        "Word structures, so its structure was preserved."
                    ),
                )
            )
            continue
        segment_texts = _replace_paragraph_with_segments(paragraph, spans)
        output_counts[block.node_id] = len(segment_texts)
        if outline_plan is not None:
            recovered_roles[block.node_id] = [role for _, _, role in outline_plan]
        reason_parts: list[str] = []
        if outline_plan is not None:
            reason_parts.append("collapsed numbered outline")
        elif "\n" in block.text and spec.split_on_manual_breaks:
            reason_parts.append("manual line breaks")
        if any(len(text) > spec.target_body_chars for text in segment_texts):
            reason_parts.append("long body text")
        changes.append(
            AutoLayoutChange(
                source_node_id=block.node_id,
                before_text=block.text,
                after_texts=segment_texts,
                reason=" and ".join(reason_parts) or "body paragraph segmentation",
            )
        )

    if not changes:
        return AutoLayoutResult(source_path, document_ir, [], warnings)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    structured = analyze_document(
        parse_docx(output_path, document_id=document_ir.document_id)
    ).document_ir
    structured.source_filename = document_ir.source_filename
    _restore_unsplit_role_assignments(
        document_ir,
        structured,
        output_counts,
        warnings,
        recovered_roles,
    )
    assert_auto_layout_integrity(document_ir, structured)
    return AutoLayoutResult(output_path, structured, list(reversed(changes)), warnings)


def assert_auto_layout_integrity(before: DocumentIR, after: DocumentIR) -> None:
    """Allow paragraph-boundary changes while preserving every logical content component."""

    comparisons: tuple[tuple[str, object, object], ...] = (
        ("main_text", _main_text(before), _main_text(after)),
        (
            "table_cell_texts",
            before.content_fingerprint.table_cell_texts,
            after.content_fingerprint.table_cell_texts,
        ),
        (
            "header_footer_texts",
            before.content_fingerprint.header_footer_texts,
            after.content_fingerprint.header_footer_texts,
        ),
        (
            "field_instructions",
            before.content_fingerprint.field_instructions,
            after.content_fingerprint.field_instructions,
        ),
        (
            "bookmark_names",
            before.content_fingerprint.bookmark_names,
            after.content_fingerprint.bookmark_names,
        ),
        (
            "image_hashes",
            before.content_fingerprint.image_hashes,
            after.content_fingerprint.image_hashes,
        ),
        (
            "relationship_signatures",
            before.content_fingerprint.relationship_signatures,
            after.content_fingerprint.relationship_signatures,
        ),
        ("non_paragraph_blocks", _non_paragraph_blocks(before), _non_paragraph_blocks(after)),
        ("section_count", len(before.sections), len(after.sections)),
        ("table_count", before.metadata.table_count, after.metadata.table_count),
        ("image_count", before.metadata.image_count, after.metadata.image_count),
        ("binary_parts", _binary_parts(before), _binary_parts(after)),
    )
    changed = [name for name, left, right in comparisons if left != right]
    if changed:
        raise AutoLayoutIntegrityError(
            "Automatic layout changed protected content components: " + ", ".join(changed)
        )


def _segment_spans(text: str, spec: AutoLayoutSpec) -> list[tuple[int, int]]:
    if not text:
        return [(0, 0)]
    line_spans: list[tuple[int, int]] = []
    if spec.split_on_manual_breaks and "\n" in text:
        cursor = 0
        for match in re.finditer(r"\n", text):
            if match.start() > cursor:
                line_spans.append((cursor, match.start()))
            cursor = match.end()
        if cursor < len(text):
            line_spans.append((cursor, len(text)))
    else:
        line_spans = [(0, len(text))]

    result: list[tuple[int, int]] = []
    for start, end in line_spans:
        result.extend(_split_long_span(text, start, end, spec))
    return result or [(0, len(text))]


def _collapsed_numbered_outline_plan(
    text: str,
    spec: AutoLayoutSpec,
) -> list[tuple[int, int, SemanticRole]] | None:
    """Recover a strongly signaled numbered outline collapsed into one Word paragraph.

    This intentionally requires consecutive Chinese major headings and matching decimal
    subheadings. It refuses partial or ambiguous outlines rather than guessing boundaries in
    ordinary prose.
    """

    if "\n" in text:
        return None

    major_matches: list[tuple[re.Match[str], int]] = []
    for match in _COLLAPSED_MAJOR_HEADING.finditer(text):
        ordinal = _chinese_ordinal(match.group()[:-1])
        if ordinal is None:
            continue
        if not major_matches:
            if match.start() > 80:
                continue
        elif text[match.start() - 1] not in _OUTLINE_BOUNDARY_PUNCTUATION:
            continue
        major_matches.append((match, ordinal))

    if len(major_matches) < 2:
        return None
    if [ordinal for _, ordinal in major_matches] != list(range(1, len(major_matches) + 1)):
        return None

    title_end = major_matches[0][0].start()
    title_text = text[:title_end].strip()
    if title_text and (
        len(title_text) > 80
        or any(character in _OUTLINE_BOUNDARY_PUNCTUATION for character in title_text)
    ):
        return None

    section_subheadings: list[list[tuple[re.Match[str], tuple[int, ...]]]] = []
    total_subheadings = 0
    for section_index, (major_match, ordinal) in enumerate(major_matches):
        section_end = (
            major_matches[section_index + 1][0].start()
            if section_index + 1 < len(major_matches)
            else len(text)
        )
        accepted: list[tuple[re.Match[str], tuple[int, ...]]] = []
        for match in _COLLAPSED_DECIMAL_HEADING.finditer(
            text,
            major_match.end(),
            section_end,
        ):
            parts = tuple(int(part) for part in match.group(1).split("."))
            if parts[0] != ordinal:
                continue
            if not accepted:
                major_title = text[major_match.end() : match.start()].strip()
                if (
                    not major_title
                    or len(major_title) > 60
                    or any(
                        character in _OUTLINE_BOUNDARY_PUNCTUATION
                        for character in major_title
                    )
                ):
                    return None
            elif text[match.start() - 1] not in _OUTLINE_BOUNDARY_PUNCTUATION:
                continue
            accepted.append((match, parts))

        second_level = [parts[1] for _, parts in accepted if len(parts) == 2]
        if not second_level or second_level != list(range(1, len(second_level) + 1)):
            return None
        section_subheadings.append(accepted)
        total_subheadings += len(accepted)

    if total_subheadings < 4:
        return None

    plan: list[tuple[int, int, SemanticRole]] = []
    if title_end > 0:
        plan.append((0, title_end, SemanticRole.TITLE))

    for section_index, ((major_match, _), subheadings) in enumerate(
        zip(major_matches, section_subheadings, strict=True)
    ):
        section_end = (
            major_matches[section_index + 1][0].start()
            if section_index + 1 < len(major_matches)
            else len(text)
        )
        first_subheading = subheadings[0][0]
        plan.append((major_match.start(), first_subheading.start(), SemanticRole.HEADING_1))

        for subheading_index, (match, parts) in enumerate(subheadings):
            region_end = (
                subheadings[subheading_index + 1][0].start()
                if subheading_index + 1 < len(subheadings)
                else section_end
            )
            body_start = _collapsed_heading_body_boundary(text, match.end(), region_end)
            if body_start is None:
                return None
            role = _decimal_heading_role(parts)
            plan.append((match.start(), body_start, role))
            plan.extend(
                (start, end, SemanticRole.BODY)
                for start, end in _split_long_span(text, body_start, region_end, spec)
            )

    if not plan or plan[0][0] != 0 or plan[-1][1] != len(text):
        return None
    if any(left[1] != right[0] for left, right in zip(plan, plan[1:], strict=False)):
        return None
    return plan


def _collapsed_heading_body_boundary(text: str, start: int, end: int) -> int | None:
    search_end = min(end, start + 48)
    candidates: list[int] = []
    for match in _BODY_OPENERS.finditer(text, start, search_end):
        title = text[start : match.start()].strip()
        if not 4 <= len(title) <= 36:
            continue
        if any(character in _OUTLINE_BOUNDARY_PUNCTUATION for character in title):
            continue
        if not title.endswith(_HEADING_TITLE_SUFFIXES):
            continue
        if end - match.start() < 12:
            continue
        candidates.append(match.start())
    return min(candidates) if candidates else None


def _decimal_heading_role(parts: tuple[int, ...]) -> SemanticRole:
    return {
        2: SemanticRole.HEADING_2,
        3: SemanticRole.HEADING_3,
        4: SemanticRole.HEADING_4,
    }[min(len(parts), 4)]


def _chinese_ordinal(value: str) -> int | None:
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if value.startswith("十") and value[1:] in digits:
        return 10 + digits[value[1:]]
    if value.endswith("十") and value[:-1] in digits:
        return digits[value[:-1]] * 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        if tens in digits and ones in digits:
            return digits[tens] * 10 + digits[ones]
    return None


def _split_long_span(
    text: str,
    start: int,
    end: int,
    spec: AutoLayoutSpec,
) -> list[tuple[int, int]]:
    if end - start <= spec.max_body_chars:
        return [(start, end)]
    boundaries = [start + match.end() for match in _SENTENCE_END.finditer(text[start:end])]
    clause_boundaries = [start + match.end() for match in _CLAUSE_END.finditer(text[start:end])]
    boundaries.append(end)
    segments: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > spec.max_body_chars:
        minimum = cursor + max(40, spec.target_body_chars // 2)
        maximum = cursor + spec.max_body_chars
        candidates = [point for point in boundaries if minimum <= point <= maximum]
        if not candidates:
            candidates = [point for point in clause_boundaries if minimum <= point <= maximum]
        if not candidates:
            return [(start, end)]
        split_at = min(candidates, key=lambda point: abs(point - (cursor + spec.target_body_chars)))
        segments.append((cursor, split_at))
        cursor = split_at
    if cursor < end:
        segments.append((cursor, end))
    return segments


def _paragraph_is_safe_to_split(paragraph: Paragraph, block: ParagraphIR) -> bool:
    if block.numbering is not None or any(
        (
            block.contains_drawing,
            block.contains_equation,
            block.contains_field,
            block.contains_hyperlink,
            block.contains_bookmark,
            block.contains_content_control,
        )
    ):
        return False
    if paragraph._p.xpath(".//w:sectPr"):
        return False
    if any(
        line_break.get(qn("w:type")) in {"page", "column"}
        for line_break in paragraph._p.iter(qn("w:br"))
    ):
        return False
    if any(child.tag not in {qn("w:pPr"), qn("w:r")} for child in paragraph._p):
        return False
    if any(
        child.tag not in _RUN_CONTENT_TAGS
        for run in paragraph._p.findall(qn("w:r"))
        for child in run
    ):
        return False
    return "".join(run.text for run in paragraph.runs) == block.text


def _replace_paragraph_with_segments(
    paragraph: Paragraph, spans: list[tuple[int, int]]
) -> list[str]:
    source_runs = [(run, run.text) for run in paragraph.runs]
    run_offsets: list[tuple[Run, str, int, int]] = []
    cursor = 0
    for run, text in source_runs:
        run_offsets.append((run, text, cursor, cursor + len(text)))
        cursor += len(text)

    parent: Any = paragraph._p.getparent()
    insertion_index = parent.index(paragraph._p)
    segment_texts: list[str] = []
    for segment_index, (start, end) in enumerate(spans):
        new_p: Any = OxmlElement("w:p")
        if paragraph._p.pPr is not None:
            new_p.append(deepcopy(paragraph._p.pPr))
        new_paragraph = Paragraph(new_p, paragraph._parent)
        text_parts: list[str] = []
        for source_run, run_text, run_start, run_end in run_offsets:
            overlap_start = max(start, run_start)
            overlap_end = min(end, run_end)
            if overlap_start >= overlap_end:
                continue
            piece = run_text[overlap_start - run_start : overlap_end - run_start]
            new_r: Any = OxmlElement("w:r")
            if source_run._r.rPr is not None:
                new_r.append(deepcopy(source_run._r.rPr))
            new_run = Run(new_r, new_paragraph)
            new_run.text = piece
            new_p.append(new_r)
            text_parts.append(piece)
        parent.insert(insertion_index + segment_index, new_p)
        segment_texts.append("".join(text_parts))
    parent.remove(paragraph._p)
    return segment_texts


def _restore_unsplit_role_assignments(
    before: DocumentIR,
    after: DocumentIR,
    output_counts: dict[str, int],
    warnings: list[PlanWarning],
    recovered_roles: dict[str, list[SemanticRole]],
) -> None:
    cursor = 0
    for source_block in before.blocks:
        count = output_counts.get(source_block.node_id, 1)
        targets = after.blocks[cursor : cursor + count]
        cursor += count
        planned_roles = recovered_roles.get(source_block.node_id)
        if planned_roles is not None and len(planned_roles) == len(targets):
            for target, role in zip(targets, planned_roles, strict=True):
                if not isinstance(target, ParagraphIR):
                    continue
                target.detected_role = role
                target.role_confidence = 0.96
                target.role_source = RoleSource.DETERMINISTIC
                target.role_evidence = ["collapsed-outline-recovery"]
            continue
        if (
            count == 1
            and isinstance(source_block, ParagraphIR)
            and targets
            and isinstance(targets[0], ParagraphIR)
            and targets[0].text == source_block.text
        ):
            target = targets[0]
            target.detected_role = source_block.detected_role
            target.role_confidence = source_block.role_confidence
            target.role_source = source_block.role_source
            target.role_evidence = list(source_block.role_evidence)
    if cursor != len(after.blocks):
        warnings.append(
            PlanWarning(
                code="AUTO_LAYOUT_ROLE_MAPPING_PARTIAL",
                message=(
                    "Some unchanged paragraph role assignments could not be mapped after "
                    "automatic layout; those paragraphs were reclassified locally."
                ),
            )
        )


def _main_text(document: DocumentIR) -> str:
    return "".join(
        block.text.replace("\r", "").replace("\n", "")
        for block in document.blocks
        if isinstance(block, ParagraphIR)
    )


def _non_paragraph_blocks(document: DocumentIR) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for block in document.blocks:
        if block.kind == "table":
            result.append(("table", repr(block.cell_texts)))
        elif block.kind == "unsupported":
            result.append(("unsupported", f"{block.qname}|{block.text_preview}"))
    return result


def _binary_parts(document: DocumentIR) -> dict[str, str]:
    return {
        part.path: part.sha256
        for part in document.package_parts
        if not part.path.lower().endswith((".xml", ".rels"))
    }
