from __future__ import annotations

from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel


class ManifestRequirement(StrictModel):
    requirement_id: str
    category: Literal[
        "style",
        "section",
        "table",
        "numbering",
        "header_footer",
    ]
    target: str
    property_path: str
    expected: object
    source_part: str
    evidence: str
    confidence: float = Field(ge=0, le=1)
    auto_applicable: bool = False


class FormatManifestSummary(StrictModel):
    requirement_count: int
    by_category: dict[str, int] = Field(default_factory=dict)
    auto_applicable_count: int = 0


class FormatManifest(StrictModel):
    schema_version: Literal["format-manifest.v1"] = "format-manifest.v1"
    document_id: str
    source_filename: str
    source_sha256: str
    requirements: list[ManifestRequirement] = Field(default_factory=list)
    summary: FormatManifestSummary
    warnings: list[str] = Field(default_factory=list)
