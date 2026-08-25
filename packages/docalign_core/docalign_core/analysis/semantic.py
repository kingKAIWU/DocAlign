from __future__ import annotations

from typing import Protocol

from pydantic import Field

from docalign_core.analysis.classifier import build_analysis_summary
from docalign_core.domain.base import StrictModel
from docalign_core.domain.document_ir import (
    AnalysisResult,
    DocumentIR,
    DocumentWarning,
    ParagraphIR,
)
from docalign_core.domain.enums import AnalysisMode, DocumentKind, RoleSource, SemanticRole


class SemanticRoleAssignment(StrictModel):
    node_id: str
    role: SemanticRole
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=240)


class SemanticAnalysisDraft(StrictModel):
    document_kind: DocumentKind = DocumentKind.OTHER
    document_kind_confidence: float = Field(default=0.5, ge=0, le=1)
    assignments: list[SemanticRoleAssignment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SemanticAnalyzerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SemanticAnalyzer(Protocol):
    provider: str
    model: str

    async def analyze(
        self, document_ir: DocumentIR, deterministic: AnalysisResult
    ) -> SemanticAnalysisDraft: ...


def merge_semantic_analysis(
    deterministic: AnalysisResult,
    draft: SemanticAnalysisDraft,
    *,
    provider: str,
    model: str,
) -> AnalysisResult:
    """Merge model semantics without overriding hard structural evidence."""

    result = deterministic.model_copy(deep=True)
    paragraphs = {
        block.node_id: block
        for block in result.document_ir.blocks
        if isinstance(block, ParagraphIR)
    }
    warnings = [warning for warning in result.warnings if warning.code != "LOW_CONFIDENCE_ROLE"]
    seen: set[str] = set()
    reviewed = 0
    for assignment in draft.assignments:
        paragraph = paragraphs.get(assignment.node_id)
        if paragraph is None:
            warnings.append(
                DocumentWarning(
                    code="SEMANTIC_NODE_IGNORED",
                    message="The semantic model referenced a node outside this document.",
                    node_id=assignment.node_id,
                )
            )
            continue
        if assignment.node_id in seen:
            warnings.append(
                DocumentWarning(
                    code="SEMANTIC_DUPLICATE_IGNORED",
                    message="A duplicate semantic assignment was ignored.",
                    node_id=assignment.node_id,
                )
            )
            continue
        seen.add(assignment.node_id)
        if paragraph.is_empty:
            continue
        reviewed += 1
        hard_evidence = paragraph.role_source == RoleSource.EXISTING_STYLE or (
            paragraph.role_source == RoleSource.DETERMINISTIC and paragraph.role_confidence >= 0.9
        )
        if hard_evidence and assignment.role != paragraph.detected_role:
            warnings.append(
                DocumentWarning(
                    code="SEMANTIC_OVERRIDE_REJECTED",
                    message="Hard structural evidence took priority over the model suggestion.",
                    node_id=paragraph.node_id,
                    details={
                        "kept_role": paragraph.detected_role.value,
                        "suggested_role": assignment.role.value,
                    },
                )
            )
            continue
        if assignment.confidence < 0.55:
            warnings.append(
                DocumentWarning(
                    code="LOW_CONFIDENCE_ROLE",
                    message=f"Semantic role confidence is {assignment.confidence:.2f}.",
                    node_id=paragraph.node_id,
                    details={"candidate_role": assignment.role.value},
                )
            )
            continue
        paragraph.detected_role = assignment.role
        paragraph.role_confidence = assignment.confidence
        paragraph.role_source = RoleSource.LLM
        paragraph.role_evidence = ["semantic-model", assignment.evidence]

    for paragraph in paragraphs.values():
        if paragraph.role_confidence < 0.75 and not paragraph.is_empty:
            warnings.append(
                DocumentWarning(
                    code="LOW_CONFIDENCE_ROLE",
                    message=f"Paragraph role confidence is {paragraph.role_confidence:.2f}.",
                    node_id=paragraph.node_id,
                    details={"candidate_role": paragraph.detected_role.value},
                )
            )
    warnings.extend(
        DocumentWarning(code="SEMANTIC_MODEL_WARNING", message=message)
        for message in draft.warnings
    )
    result.document_ir.warnings = _dedupe_warnings(warnings)
    result.warnings = result.document_ir.warnings
    result.summary = build_analysis_summary(
        result.document_ir,
        analysis_mode=AnalysisMode.SMART,
        document_kind=draft.document_kind,
        document_kind_confidence=draft.document_kind_confidence,
        model_reviewed_paragraphs=reviewed,
        model_provider=provider,
        model_name=model,
    )
    return result


def _dedupe_warnings(warnings: list[DocumentWarning]) -> list[DocumentWarning]:
    seen: set[tuple[str, str | None]] = set()
    result: list[DocumentWarning] = []
    for warning in warnings:
        key = (warning.code, warning.node_id)
        if key not in seen:
            seen.add(key)
            result.append(warning)
    return result
