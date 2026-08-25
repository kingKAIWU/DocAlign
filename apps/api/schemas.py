from __future__ import annotations

from typing import Any

from docalign_core.domain.document_ir import RoleOverride
from docalign_core.domain.enums import AnalysisMode
from docalign_core.domain.formatting_spec import FormattingSpec
from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleOverrideRequest(ApiModel):
    overrides: list[RoleOverride] = Field(default_factory=list)


class AnalyzeRequest(ApiModel):
    mode: AnalysisMode = AnalysisMode.DETERMINISTIC


class TextDocumentRequest(ApiModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="未命名文档.docx", min_length=1, max_length=512)


class StructuredSpecRequest(ApiModel):
    document_id: str | None = None
    spec: FormattingSpec


class CompileSpecRequest(ApiModel):
    document_id: str | None = None
    analysis_id: str | None = None
    instruction: str = Field(min_length=1, max_length=20_000)
    apply_preset: bool = False


class ValidateSpecRequest(ApiModel):
    spec: dict[str, Any]


class JobCreateRequest(ApiModel):
    document_id: str
    analysis_id: str
    spec_id: str


class ComplianceRequest(ApiModel):
    analysis_id: str
    spec_id: str
