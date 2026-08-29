from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from docalign_core.domain.base import StrictModel
from docalign_core.domain.formatting_spec import FormattingSpec


class RulePackApprovalStatus(StrEnum):
    DRAFT = "draft"
    LOCALLY_APPROVED = "locally_approved"


class RulePackArtifact(StrictModel):
    """Portable, integrity-checked snapshot of one immutable rule-pack revision."""

    schema_version: Literal["rule-pack.v1"] = "rule-pack.v1"
    pack_id: str
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    scope_label: str = Field(min_length=1, max_length=240)
    revision: int = Field(ge=1)
    approval_status: RulePackApprovalStatus = RulePackApprovalStatus.DRAFT
    approval_note: str | None = Field(default=None, max_length=1_000)
    change_note: str = Field(min_length=1, max_length=1_000)
    restored_from_revision: int | None = Field(default=None, ge=1)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    spec: FormattingSpec

    @model_validator(mode="after")
    def validate_integrity_and_approval(self) -> RulePackArtifact:
        if self.spec_sha256 != formatting_spec_sha256(self.spec):
            raise ValueError("spec_sha256 does not match the embedded FormattingSpec")
        if (
            self.approval_status == RulePackApprovalStatus.LOCALLY_APPROVED
            and not (self.approval_note or "").strip()
        ):
            raise ValueError("locally approved revisions require an approval note")
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


def _canonical_value(value: object) -> object:
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
