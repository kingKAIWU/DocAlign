from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from docalign_core.domain.base import StrictModel
from docalign_core.domain.document_ir import DocumentProcessingBoundary
from docalign_core.domain.enums import SemanticRole, Severity
from docalign_core.domain.formatting_spec import (
    RulePackClaimLevel,
    RulePackCoverageItem,
    RulePackReference,
    SpecSource,
)

CONTENT_INTEGRITY_CODES = frozenset(
    {
        "CONTENT_INTEGRITY_FAILED",
        "DOCUMENT_STRUCTURE_MISMATCH",
        "PROTECTED_PACKAGE_PART_CHANGED",
    }
)

DOCUMENT_FEATURE_LABELS = {
    "field": "动态字段、目录或交叉引用",
    "header_footer_field": "页眉页脚动态字段",
    "equation": "公式",
    "drawing": "图片或绘图对象",
    "hyperlink": "超链接",
    "bookmark": "书签",
    "content_control": "内容控件",
    "merged_table": "合并单元格表格",
    "nested_table": "嵌套表格",
    "unknown_ooxml": "未识别的顶层 Word 结构",
    "text_box": "文本框",
    "footnote": "脚注",
    "endnote": "尾注",
    "comment": "批注",
    "embedded_object": "嵌入对象或 ActiveX",
    "macro": "宏项目",
    "external_link": "外部链接关系",
    "multiple_sections": "多分节版式",
}

DOCUMENT_HANDLING_LABELS = {
    "format_and_validate": "参与格式化并验证",
    "preserve_and_validate": "保留并验证完整性",
    "preserve_only": "只保留，不做专门格式化",
}


class OperationType(StrEnum):
    SPLIT_BODY_PARAGRAPH = "split_body_paragraph"
    SET_SECTION_LAYOUT = "set_section_layout"
    CREATE_OR_UPDATE_STYLE = "create_or_update_style"
    ASSIGN_PARAGRAPH_STYLE = "assign_paragraph_style"
    SET_PARAGRAPH_FORMAT = "set_paragraph_format"
    SET_RUN_FONT = "set_run_font"
    SET_RUN_SIZE = "set_run_size"
    SET_RUN_EMPHASIS = "set_run_emphasis"
    SET_TABLE_FORMAT = "set_table_format"
    SET_CELL_FORMAT = "set_cell_format"
    ALIGN_IMAGE_PARAGRAPH = "align_image_paragraph"
    FORMAT_HEADER = "format_header"
    FORMAT_FOOTER = "format_footer"
    INSERT_PAGE_NUMBER = "insert_page_number"
    NORMALIZE_DOCUMENT_VISUALS = "normalize_document_visuals"


class FormattingOperation(StrictModel):
    operation_id: str
    node_id: str | None = None
    locator: str | None = None
    target_role: SemanticRole | None = None
    operation_type: OperationType
    properties: dict[str, object] = Field(default_factory=dict)
    reason: str


class PlanWarning(StrictModel):
    code: str
    message: str
    node_id: str | None = None
    locator: str | None = None


class FormattingPlan(StrictModel):
    schema_version: Literal["formatting-plan.v1"] = "formatting-plan.v1"
    plan_id: str
    document_id: str
    operations: list[FormattingOperation] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)


class MutationRecord(StrictModel):
    operation_id: str
    node_id: str | None = None
    locator: str | None = None
    property_path: str
    before: object | None = None
    after: object | None = None
    status: Literal["changed", "already_compliant", "skipped", "warning"]


class ValidationIssue(StrictModel):
    code: str
    severity: Severity
    message: str
    node_id: str | None = None
    locator: str | None = None
    details: dict[str, object] = Field(default_factory=dict)


class ValidationReport(StrictModel):
    schema_version: Literal["validation-report.v1"] = "validation-report.v1"
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    content_fingerprint_before: str | None = None
    content_fingerprint_after: str | None = None

    @property
    def fatal(self) -> bool:
        return any(issue.severity == Severity.FATAL for issue in self.issues)


class AuditSummary(StrictModel):
    paragraphs: int
    tables: int
    images: int
    classified_blocks: int
    unknown_blocks: int
    format_operations: int
    changed_mutations: int
    validation_failures: int
    paragraphs_before: int | None = None
    paragraphs_after: int | None = None
    auto_layout_splits: int = 0


class AppliedPresetEvidence(StrictModel):
    preset_id: str
    preset_name: str
    pack_version: str
    claim_level: RulePackClaimLevel
    scope_label: str
    maintained_by: str
    last_reviewed_on: date
    source_references: list[RulePackReference] = Field(default_factory=list)
    catalog_spec_sha256: str
    matches_catalog_spec: bool
    automated_requirements: list[RulePackCoverageItem] = Field(default_factory=list)
    review_requirements: list[RulePackCoverageItem] = Field(default_factory=list)
    acceptance_fixture_id: str | None = None
    acceptance_last_passed_on: date | None = None
    acceptance_automated_checks: list[str] = Field(default_factory=list)
    acceptance_manual_checks: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AuditExecutionEvidence(StrictModel):
    engine_version: str
    spec_sha256: str
    applied_preset: AppliedPresetEvidence | None = None


class ProcessingBoundaryAcknowledgmentMethod(StrEnum):
    NOT_REQUIRED = "not_required"
    NOT_RECORDED = "not_recorded"
    EXPLICIT_SINGLE_JOB = "explicit_single_job"
    EXPLICIT_BATCH = "explicit_batch"
    EXPLICIT_CLI = "explicit_cli"


class ProcessingBoundaryAcknowledgment(StrictModel):
    required: bool
    acknowledged: bool
    method: ProcessingBoundaryAcknowledgmentMethod
    boundary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_feature_codes: list[str] = Field(default_factory=list)
    acknowledged_at: datetime | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> ProcessingBoundaryAcknowledgment:
        explicit_methods = {
            ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_SINGLE_JOB,
            ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_BATCH,
            ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_CLI,
        }
        if self.acknowledged != (self.method in explicit_methods):
            raise ValueError("Acknowledgment state does not match its evidence method.")
        if self.acknowledged and self.acknowledged_at is None:
            raise ValueError("Explicit processing-boundary acknowledgment requires a timestamp.")
        if self.required and not self.review_feature_codes:
            raise ValueError("Required processing-boundary review must identify feature codes.")
        if self.method == ProcessingBoundaryAcknowledgmentMethod.NOT_REQUIRED and self.required:
            raise ValueError("A required processing boundary cannot be marked not required.")
        if self.method == ProcessingBoundaryAcknowledgmentMethod.NOT_RECORDED and not self.required:
            raise ValueError("Unrecorded acknowledgment only applies to a required boundary.")
        return self


class AuditReport(StrictModel):
    schema_version: Literal["audit-report.v1"] = "audit-report.v1"
    job_id: str
    source_file: str
    output_file: str | None = None
    source_sha256: str
    output_sha256: str | None = None
    summary: AuditSummary
    roles: dict[str, int] = Field(default_factory=dict)
    mutations: list[MutationRecord] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)
    validation: ValidationReport
    assumptions: list[str] = Field(default_factory=list)
    spec_source: SpecSource | None = None
    execution_evidence: AuditExecutionEvidence | None = None
    source_processing_boundary: DocumentProcessingBoundary | None = None
    source_processing_boundary_acknowledgment: ProcessingBoundaryAcknowledgment | None = None

    def to_markdown(self) -> str:
        status = "通过" if self.validation.valid else "需要检查"
        lines = [
            "# DocAlign 格式化审计",
            "",
            f"- 任务：`{self.job_id}`",
            f"- 源文件：`{self.source_file}`",
            f"- 输出文件：`{self.output_file or '未发布'}`",
            f"- 验证状态：**{status}**",
            "- 段落 / 表格 / 图片："
            f"{self.summary.paragraphs} / {self.summary.tables} / {self.summary.images}",
            (
                "- 自动排版分段："
                f"{self.summary.auto_layout_splits} 处"
                f"（{self.summary.paragraphs_before} → {self.summary.paragraphs_after} 段）"
            ),
            f"- 格式操作：{self.summary.format_operations}",
            f"- 实际变更：{self.summary.changed_mutations}",
            f"- 规则来源：{self.spec_source.type.value if self.spec_source else '未知'}",
            *(
                [
                    f"- 引擎版本：{self.execution_evidence.engine_version}",
                    f"- 规则 SHA-256：`{self.execution_evidence.spec_sha256}`",
                ]
                if self.execution_evidence
                else []
            ),
        ]
        boundary = self.source_processing_boundary
        if boundary:
            lines.extend(
                [
                    "",
                    "## 源文档处理边界",
                    "",
                    f"- 检测到的复杂内容类型：{boundary.detected_feature_count} 类",
                    f"- 需要人工核对：{boundary.review_feature_count} 类",
                    "- 说明：保留或验证通过不等同于完成专门格式化；"
                    "交付前仍应按清单在 Word/WPS 中核对。",
                ]
            )
            acknowledgment = self.source_processing_boundary_acknowledgment
            if acknowledgment:
                acknowledgment_labels = {
                    ProcessingBoundaryAcknowledgmentMethod.NOT_REQUIRED: "无需确认",
                    ProcessingBoundaryAcknowledgmentMethod.NOT_RECORDED: "未记录",
                    ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_SINGLE_JOB: (
                        "单文档任务明确确认"
                    ),
                    ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_BATCH: "批处理策略明确确认",
                    ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_CLI: "命令行任务明确确认",
                }
                lines.extend(
                    [
                        f"- 确认记录：{acknowledgment_labels[acknowledgment.method]}",
                        f"- 边界快照 SHA-256：`{acknowledgment.boundary_sha256}`",
                    ]
                )
                if acknowledgment.acknowledged_at:
                    lines.append(
                        "- 确认时间："
                        f"{acknowledgment.acknowledged_at.isoformat()}"
                    )
            for item in boundary.items:
                review = "需人工核对" if item.review_required else "信息提示"
                locator_text = f" · 位置：{', '.join(item.locators)}" if item.locators else ""
                feature_label = DOCUMENT_FEATURE_LABELS.get(item.code, item.code)
                handling_label = DOCUMENT_HANDLING_LABELS.get(
                    item.handling.value,
                    item.handling.value,
                )
                lines.append(
                    f"- {feature_label}（`{item.code}`）· {handling_label} · "
                    f"{item.count} 处 · {review}"
                    f"{locator_text}"
                )
        lines.extend(["", "## 角色统计", ""])
        lines.extend(f"- {role}: {count}" for role, count in sorted(self.roles.items()))
        preset = self.execution_evidence.applied_preset if self.execution_evidence else None
        if preset:
            match_label = (
                "与目录原始规则一致"
                if preset.matches_catalog_spec
                else "已偏离目录原始规则"
            )
            lines.extend(
                [
                    "",
                    "## 规则覆盖与交付边界",
                    "",
                    f"- 规则：{preset.preset_name}（`{preset.preset_id}`，v{preset.pack_version}）",
                    f"- 声明级别：{preset.claim_level.value}",
                    f"- 适用范围：{preset.scope_label}",
                    f"- 规则一致性：**{match_label}**",
                    f"- 目录规则 SHA-256：`{preset.catalog_spec_sha256}`",
                    f"- 自动条款：{len(preset.automated_requirements)} 项",
                    f"- 人工或暂不支持条款：{len(preset.review_requirements)} 项",
                ]
            )
            if preset.source_references:
                lines.extend(["", "### 公开来源", ""])
                lines.extend(
                    f"- [{reference.title}]({reference.url})"
                    f"{f' · {reference.version}' if reference.version else ''}"
                    for reference in preset.source_references
                )
            if not preset.matches_catalog_spec:
                lines.extend(
                    [
                        "",
                        "> 当前执行规则已被修改，目录中的自动覆盖与验收结论不能直接代表本次输出。",
                    ]
                )
            if preset.review_requirements:
                lines.extend(["", "### 人工复核与暂不支持条款", ""])
                lines.extend(
                    f"- `{item.status.value}` · `{item.requirement_id}` · "
                    f"{item.requirement}：{item.implementation_note}"
                    for item in preset.review_requirements
                )
            if preset.acceptance_manual_checks:
                lines.extend(["", "### 交付前人工验收清单", ""])
                lines.extend(f"- {item}" for item in preset.acceptance_manual_checks)
            if preset.limitations:
                lines.extend(["", "### 未覆盖与限制", ""])
                lines.extend(f"- {item}" for item in preset.limitations)
        changed = [mutation for mutation in self.mutations if mutation.status == "changed"]
        if changed:
            lines.extend(["", "## 实际格式变更", ""])
            for mutation in changed[:50]:
                lines.append(
                    f"- `{mutation.locator or '全文'}` · `{mutation.property_path}`："
                    f"{_markdown_value(mutation.before)} → {_markdown_value(mutation.after)}"
                )
            if len(changed) > 50:
                lines.append(f"- 其余 {len(changed) - 50} 项请查看 `audit.json`。")
        lines.extend(["", "## 警告与验证问题", ""])
        if not self.warnings and not self.validation.issues:
            lines.append("无。")
        else:
            lines.extend(
                f"- `{warning.code}`{_markdown_locator(warning.locator)}：{warning.message}"
                for warning in self.warnings
            )
            lines.extend(
                f"- `{issue.severity.value}/{issue.code}`"
                f"{_markdown_locator(issue.locator)}：{issue.message}"
                for issue in self.validation.issues
            )
        if self.assumptions:
            lines.extend(["", "## 编译假设", ""])
            lines.extend(f"- {item}" for item in self.assumptions)
        return "\n".join(lines) + "\n"


def _markdown_locator(locator: str | None) -> str:
    return f"（`{locator}`）" if locator else ""


def _markdown_value(value: object | None) -> str:
    if value is None:
        return "未设置"
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    compact = " ".join(rendered.split()).replace("`", "'")
    return f"{compact[:157]}…" if len(compact) > 160 else compact
