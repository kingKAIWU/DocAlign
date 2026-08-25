from __future__ import annotations

import hashlib

from docalign_core.domain.formatting_spec import SpecSource, SpecSourceType, default_academic_spec
from docalign_core.llm.base import DocumentSummary, RequirementCompilationResult


class MockRequirementInterpreter:
    async def compile_requirements(
        self,
        user_text: str,
        document_summary: DocumentSummary | None = None,
    ) -> RequirementCompilationResult:
        spec = default_academic_spec()
        assumption = "Mock interpreter returned the neutral Chinese academic preset."
        spec.source = SpecSource(
            type=SpecSourceType.NATURAL_LANGUAGE,
            instruction_hash=hashlib.sha256(user_text.encode()).hexdigest(),
            compiler_version="mock.v1",
            provider="mock",
            model="mock",
            assumptions=[assumption],
        )
        return RequirementCompilationResult(
            spec=spec,
            assumptions=[assumption],
            provider="mock",
            model="mock",
        )
