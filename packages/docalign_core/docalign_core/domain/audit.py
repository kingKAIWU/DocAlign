from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.enums import SemanticRole, Severity
from docalign_core.domain.formatting_spec import SpecSource

CONTENT_INTEGRITY_CODES = frozenset(
    {
        "CONTENT_INTEGRITY_FAILED",
        "DOCUMENT_STRUCTURE_MISMATCH",
        "PROTECTED_PACKAGE_PART_CHANGED",
    }
)


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
            "",
            "## 角色统计",
            "",
        ]
        lines.extend(f"- {role}: {count}" for role, count in sorted(self.roles.items()))
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
