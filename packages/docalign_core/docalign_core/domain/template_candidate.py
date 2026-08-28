from __future__ import annotations

from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import FormattingSpec


class TemplateRoleMapping(StrictModel):
    role: SemanticRole
    source_style_name: str
    paragraph_count: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    included_properties: list[str] = Field(default_factory=list)


class TemplateCandidateSummary(StrictModel):
    source_requirement_count: int = Field(ge=0)
    auto_applicable_requirement_count: int = Field(ge=0)
    applied_requirement_count: int = Field(ge=0)
    mapped_role_count: int = Field(ge=0)
    coverage_percent: float = Field(ge=0, le=100)


class TemplateRuleCandidate(StrictModel):
    schema_version: Literal["template-rule-candidate.v1"] = "template-rule-candidate.v1"
    source_filename: str
    source_sha256: str
    safe_to_apply: bool
    spec: FormattingSpec
    summary: TemplateCandidateSummary
    role_mappings: list[TemplateRoleMapping] = Field(default_factory=list)
    applied_requirement_ids: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
