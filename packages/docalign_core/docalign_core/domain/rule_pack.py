from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from docalign_core.domain.base import StrictModel
from docalign_core.domain.formatting_spec import FormattingSpec


class RulePackApprovalStatus(StrEnum):
    DRAFT = "draft"
    LOCALLY_APPROVED = "locally_approved"


class RulePackImportSource(StrictModel):
    """Normalized provenance retained when a portable artifact is imported."""

    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pack_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    name: str = Field(min_length=1, max_length=120)
    scope_label: str = Field(min_length=1, max_length=240)
    revision: int = Field(ge=1, le=2_147_483_647)
    approval_status: RulePackApprovalStatus
    approval_note: str | None = Field(default=None, max_length=1_000)
    change_note: str = Field(min_length=1, max_length=1_000)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @field_validator("name", "scope_label", "change_note")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain visible characters")
        return value

    @model_validator(mode="after")
    def validate_approval(self) -> RulePackImportSource:
        if (
            self.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            and not (self.approval_note or "").strip()
        ):
            raise ValueError("locally approved import sources require an approval note")
        return self


class RulePackArtifact(StrictModel):
    """Portable, integrity-checked snapshot of one immutable rule-pack revision."""

    schema_version: Literal["rule-pack.v1"] = "rule-pack.v1"
    pack_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    scope_label: str = Field(min_length=1, max_length=240)
    revision: int = Field(ge=1, le=2_147_483_647)
    approval_status: RulePackApprovalStatus = RulePackApprovalStatus.DRAFT
    approval_note: str | None = Field(default=None, max_length=1_000)
    change_note: str = Field(min_length=1, max_length=1_000)
    restored_from_revision: int | None = Field(default=None, ge=1)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    spec: FormattingSpec
    import_source: RulePackImportSource | None = None

    @field_validator("name", "scope_label", "change_note")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain visible characters")
        return value

    @model_validator(mode="after")
    def validate_integrity_and_approval(self) -> RulePackArtifact:
        if self.spec_sha256 != formatting_spec_sha256(self.spec):
            raise ValueError("spec_sha256 does not match the embedded FormattingSpec")
        if (
            self.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            and not (self.approval_note or "").strip()
        ):
            raise ValueError("locally approved revisions require an approval note")
        if self.import_source and self.import_source.spec_sha256 != self.spec_sha256:
            raise ValueError("import source spec_sha256 does not match the embedded FormattingSpec")
        return self


def canonical_formatting_spec_json(spec: FormattingSpec) -> str:
    return json.dumps(
        _canonical_value(spec.model_dump(mode="python")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def formatting_spec_sha256(spec: FormattingSpec) -> str:
    payload = canonical_formatting_spec_json(spec).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_rule_pack_artifact_json(artifact: RulePackArtifact) -> str:
    """Return the normalized portable-artifact representation used for import deduplication."""

    return json.dumps(
        _canonical_value(artifact.model_dump(mode="python")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def rule_pack_artifact_sha256(artifact: RulePackArtifact) -> str:
    payload = canonical_rule_pack_artifact_json(artifact).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        normalized = [_canonical_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
