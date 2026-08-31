from __future__ import annotations

import json
from pathlib import Path

from docalign_core import __version__
from docalign_core.analysis.classifier import count_reviewable_unknowns
from docalign_core.analysis.processing_boundary import build_processing_boundary
from docalign_core.docx.parser import parse_docx
from docalign_core.docx.safety import sha256_file
from docalign_core.domain.audit import (
    AppliedPresetEvidence,
    AuditExecutionEvidence,
    AuditReport,
    AuditSummary,
    FormattingOperation,
    FormattingPlan,
    MutationRecord,
    OperationType,
)
from docalign_core.domain.base import StrictModel
from docalign_core.domain.document_ir import DocumentIR, ParagraphIR
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    RulePackCoverageStatus,
    cleanup_preset_catalog,
)
from docalign_core.domain.rule_pack import formatting_spec_sha256
from docalign_core.engine.auto_layout import (
    AutoLayoutIntegrityError,
    apply_auto_layout,
)
from docalign_core.engine.formatter import FormattingEngine, atomic_promote
from docalign_core.engine.planner import build_formatting_plan, role_counts
from docalign_core.validation.validator import DocumentValidator


class ProcessingFailure(RuntimeError):
    def __init__(self, code: str, message: str, audit: AuditReport | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.audit = audit


class ProcessResult(StrictModel):
    output_path: str
    audit_json_path: str
    audit_markdown_path: str
    plan: FormattingPlan
    audit: AuditReport
    repair_applied: bool = False
    auto_layout_splits: int = 0


def process_document(
    source_path: Path,
    document_ir: DocumentIR,
    spec: FormattingSpec,
    output_path: Path,
    *,
    job_id: str,
    artifact_dir: Path | None = None,
) -> ProcessResult:
    artifact_dir = artifact_dir or output_path.parent
    artifact_dir.mkdir(parents=True, exist_ok=True)
    original_ir = document_ir
    source_locator_by_node = {block.node_id: block.locator for block in original_ir.blocks}
    layout_path = artifact_dir / f".{output_path.stem}.{job_id}.layout.docx"
    try:
        layout = apply_auto_layout(source_path, document_ir, spec.auto_layout, layout_path)
    except AutoLayoutIntegrityError as exc:
        layout_path.unlink(missing_ok=True)
        raise ProcessingFailure(
            "AUTO_LAYOUT_INTEGRITY_FAILED",
            "Automatic layout was stopped because protected content did not remain identical.",
        ) from exc

    working_source = layout.source_path
    document_ir = layout.document_ir
    plan = build_formatting_plan(document_ir, spec)
    layout_operations = [
        FormattingOperation(
            operation_id=f"layout-op-{index:06d}",
            node_id=change.source_node_id,
            locator=source_locator_by_node.get(change.source_node_id),
            target_role=SemanticRole.BODY,
            operation_type=OperationType.SPLIT_BODY_PARAGRAPH,
            properties={
                "segment_count": len(change.after_texts),
                "reason": change.reason,
            },
            reason="Split safe continuous body text into real Word paragraphs.",
        )
        for index, change in enumerate(layout.changes, start=1)
    ]
    plan.operations = [*layout_operations, *plan.operations]
    plan.warnings = [*layout.warnings, *plan.warnings]
    plan_path = artifact_dir / "formatting_plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    temp_path = artifact_dir / f".{output_path.stem}.{job_id}.tmp.docx"
    repair_path = artifact_dir / f".{output_path.stem}.{job_id}.repair.docx"
    engine = FormattingEngine()
    execution = engine.apply(working_source, document_ir, spec, plan, temp_path)
    validator = DocumentValidator()
    validation = validator.validate(temp_path, spec, document_ir)
    mutations = [
        MutationRecord(
            operation_id=f"layout-op-{index:06d}",
            node_id=change.source_node_id,
            locator=source_locator_by_node.get(change.source_node_id),
            property_path="paragraph.structure",
            before=change.before_text,
            after=change.after_texts,
            status="changed",
        )
        for index, change in enumerate(layout.changes, start=1)
    ]
    mutations.extend(execution.mutations)
    warnings = list(execution.warnings)
    repair_applied = False

    if (
        not validation.valid
        and not validation.fatal
        and spec.behavior.auto_repair
        and spec.behavior.validation_passes > 1
    ):
        repair_applied = True
        repaired_ir = parse_docx(temp_path, document_id=document_ir.document_id)
        _copy_role_assignments(document_ir, repaired_ir)
        repair_plan = build_formatting_plan(repaired_ir, spec)
        repair_execution = engine.apply(temp_path, repaired_ir, spec, repair_plan, repair_path)
        mutations.extend(repair_execution.mutations)
        warnings.extend(repair_execution.warnings)
        validation = validator.validate(repair_path, spec, document_ir)
        temp_path.unlink(missing_ok=True)
        temp_path = repair_path

    roles = role_counts(document_ir)
    classified = sum(count for role, count in roles.items() if role != "unknown")
    audit = AuditReport(
        job_id=job_id,
        source_file=original_ir.source_filename,
        output_file=output_path.name if not validation.fatal else None,
        source_sha256=original_ir.source_sha256,
        output_sha256=sha256_file(temp_path) if temp_path.exists() else None,
        summary=AuditSummary(
            paragraphs=document_ir.metadata.paragraph_count,
            tables=document_ir.metadata.table_count,
            images=document_ir.metadata.image_count,
            classified_blocks=classified,
            unknown_blocks=count_reviewable_unknowns(document_ir),
            format_operations=len(plan.operations),
            changed_mutations=sum(item.status == "changed" for item in mutations),
            validation_failures=sum(
                issue.severity.value in {"fatal", "error"} for issue in validation.issues
            ),
            paragraphs_before=original_ir.metadata.paragraph_count,
            paragraphs_after=document_ir.metadata.paragraph_count,
            auto_layout_splits=len(layout.changes),
        ),
        roles=roles,
        mutations=mutations,
        warnings=warnings,
        validation=validation,
        assumptions=spec.source.assumptions,
        spec_source=spec.source,
        execution_evidence=_execution_evidence(spec),
        source_processing_boundary=build_processing_boundary(original_ir),
    )
    audit_json_path = artifact_dir / "audit.json"
    audit_markdown_path = artifact_dir / "audit.md"
    audit_json_path.write_text(
        json.dumps(audit.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    audit_markdown_path.write_text(audit.to_markdown(), encoding="utf-8")

    if validation.fatal:
        temp_path.unlink(missing_ok=True)
        layout_path.unlink(missing_ok=True)
        raise ProcessingFailure(
            "CONTENT_INTEGRITY_FAILED",
            "Fatal content or package validation failed; output was not published.",
            audit,
        )
    if not validation.valid:
        temp_path.unlink(missing_ok=True)
        layout_path.unlink(missing_ok=True)
        raise ProcessingFailure(
            "OUTPUT_VALIDATION_FAILED",
            "Output formatting validation failed after the configured repair pass.",
            audit,
        )
    atomic_promote(temp_path, output_path)
    layout_path.unlink(missing_ok=True)
    audit.output_sha256 = sha256_file(output_path)
    audit_json_path.write_text(
        json.dumps(audit.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ProcessResult(
        output_path=str(output_path),
        audit_json_path=str(audit_json_path),
        audit_markdown_path=str(audit_markdown_path),
        plan=plan,
        audit=audit,
        repair_applied=repair_applied,
        auto_layout_splits=len(layout.changes),
    )


def _copy_role_assignments(source: DocumentIR, target: DocumentIR) -> None:
    source_paragraphs = {
        block.index: block for block in source.blocks if isinstance(block, ParagraphIR)
    }
    for block in target.blocks:
        if not isinstance(block, ParagraphIR):
            continue
        previous = source_paragraphs.get(block.index)
        if previous is None:
            continue
        block.detected_role = previous.detected_role
        block.role_confidence = previous.role_confidence
        block.role_source = previous.role_source
        block.role_evidence = list(previous.role_evidence)


def _execution_evidence(spec: FormattingSpec) -> AuditExecutionEvidence:
    spec_sha256 = formatting_spec_sha256(spec)
    preset_id = spec.source.preset_id
    catalog_item = next(
        (item for item in cleanup_preset_catalog() if item.preset_id == preset_id),
        None,
    )
    if catalog_item is None:
        return AuditExecutionEvidence(
            engine_version=__version__,
            spec_sha256=spec_sha256,
        )

    metadata = catalog_item.metadata
    acceptance = metadata.acceptance_evidence
    catalog_spec_sha256 = formatting_spec_sha256(catalog_item.spec)
    return AuditExecutionEvidence(
        engine_version=__version__,
        spec_sha256=spec_sha256,
        applied_preset=AppliedPresetEvidence(
            preset_id=catalog_item.preset_id,
            preset_name=catalog_item.name,
            pack_version=metadata.pack_version,
            claim_level=metadata.claim_level,
            scope_label=metadata.scope_label,
            maintained_by=metadata.maintained_by,
            last_reviewed_on=metadata.last_reviewed_on,
            source_references=metadata.source_references,
            catalog_spec_sha256=catalog_spec_sha256,
            matches_catalog_spec=spec_sha256 == catalog_spec_sha256,
            automated_requirements=[
                item
                for item in metadata.coverage_items
                if item.status == RulePackCoverageStatus.AUTOMATED
            ],
            review_requirements=[
                item
                for item in metadata.coverage_items
                if item.status != RulePackCoverageStatus.AUTOMATED
            ],
            acceptance_fixture_id=acceptance.fixture_id if acceptance else None,
            acceptance_last_passed_on=acceptance.last_passed_on if acceptance else None,
            acceptance_automated_checks=acceptance.automated_checks if acceptance else [],
            acceptance_manual_checks=acceptance.manual_checks if acceptance else [],
            limitations=metadata.limitations,
        ),
    )
