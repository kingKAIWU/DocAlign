from __future__ import annotations

from typing import Protocol

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.formatting_spec import FormattingSpec


class DocumentSummary(StrictModel):
    paragraph_count: int
    table_count: int
    image_count: int
    existing_styles: list[str] = Field(default_factory=list)
    detected_roles: dict[str, int] = Field(default_factory=dict)
    analysis_mode: str = "deterministic"
    document_kind: str | None = None


class RequirementCompilationResult(StrictModel):
    spec: FormattingSpec
    applied_capabilities: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unsupported_requests: list[str] = Field(default_factory=list)
    provider: str
    model: str


class RequirementCompilationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RequirementInterpreter(Protocol):
    async def compile_requirements(
        self,
        user_text: str,
        document_summary: DocumentSummary | None = None,
    ) -> RequirementCompilationResult: ...
