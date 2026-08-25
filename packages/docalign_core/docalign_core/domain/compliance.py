from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from docalign_core.domain.audit import ValidationIssue, ValidationReport
from docalign_core.domain.base import StrictModel


class ComplianceSummary(StrictModel):
    total_violations: int
    returned_violations: int
    affected_locators: int
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_code: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False


class ComplianceReport(StrictModel):
    schema_version: Literal["compliance-report.v1"] = "compliance-report.v1"
    document_id: str
    analysis_id: str
    spec_id: str
    compliant: bool
    summary: ComplianceSummary
    violations: list[ValidationIssue] = Field(default_factory=list)
    content_fingerprint: str | None = None


def build_compliance_report(
    validation: ValidationReport,
    *,
    document_id: str,
    analysis_id: str,
    spec_id: str,
    max_violations: int = 250,
) -> ComplianceReport:
    issues = list(validation.issues)
    returned = issues[:max_violations]
    return ComplianceReport(
        document_id=document_id,
        analysis_id=analysis_id,
        spec_id=spec_id,
        compliant=validation.valid,
        summary=ComplianceSummary(
            total_violations=len(issues),
            returned_violations=len(returned),
            affected_locators=len({item.locator for item in issues if item.locator}),
            by_severity=dict(Counter(item.severity.value for item in issues)),
            by_code=dict(Counter(item.code for item in issues)),
            truncated=len(returned) < len(issues),
        ),
        violations=returned,
        content_fingerprint=validation.content_fingerprint_after,
    )
