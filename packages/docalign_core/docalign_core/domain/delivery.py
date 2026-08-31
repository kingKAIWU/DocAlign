from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from docalign_core.domain.base import StrictModel


class DeliveryPackageKind(StrEnum):
    JOB = "job"
    BATCH = "batch"


class DeliveryPayloadRole(StrEnum):
    OUTPUT_DOCUMENT = "output_document"
    AUDIT_JSON = "audit_json"
    AUDIT_MARKDOWN = "audit_markdown"
    BATCH_AUDIT = "batch_audit"


class DeliverySignatureStatus(StrEnum):
    NOT_SIGNED = "not_signed"


class DeliveryPayloadFile(StrictModel):
    path: str
    role: DeliveryPayloadRole
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    job_id: str | None = None

    @model_validator(mode="after")
    def validate_path(self) -> DeliveryPayloadFile:
        _validate_payload_path(self.path)
        if self.role != DeliveryPayloadRole.BATCH_AUDIT and not self.job_id:
            raise ValueError("Job delivery payloads must identify their processing job.")
        if self.role == DeliveryPayloadRole.BATCH_AUDIT and self.job_id is not None:
            raise ValueError("A batch audit payload cannot identify a single job.")
        return self


class DeliveryPackageItem(StrictModel):
    position: int = Field(ge=1)
    item_id: str | None = None
    job_id: str
    source_filename: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_path: str
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_json_path: str
    audit_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_markdown_path: str
    audit_markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_passed: bool
    content_integrity_passed: bool
    structure_review_items: int = Field(ge=0)
    delivery_review_items: int = Field(ge=0)
    source_review_features: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_paths(self) -> DeliveryPackageItem:
        for path in (self.output_path, self.audit_json_path, self.audit_markdown_path):
            _validate_payload_path(path)
        return self


class DeliveryPackageManifest(StrictModel):
    schema_version: Literal["delivery-package.v1"] = "delivery-package.v1"
    package_kind: DeliveryPackageKind
    package_id: str
    created_at: datetime
    application_version: str
    checksum_algorithm: Literal["sha256"] = "sha256"
    signature_status: DeliverySignatureStatus = DeliverySignatureStatus.NOT_SIGNED
    integrity_scope: Literal["payload_and_tags"] = "payload_and_tags"
    payload_files: list[DeliveryPayloadFile]
    items: list[DeliveryPackageItem]
    batch_audit_path: str | None = None

    @model_validator(mode="after")
    def validate_package(self) -> DeliveryPackageManifest:
        if not self.payload_files:
            raise ValueError("A delivery package must contain payload files.")
        if not self.items:
            raise ValueError("A delivery package must contain at least one completed item.")
        payload_by_path = {item.path: item for item in self.payload_files}
        if len(payload_by_path) != len(self.payload_files):
            raise ValueError("Delivery payload paths must be unique.")
        positions = [item.position for item in self.items]
        if len(set(positions)) != len(positions):
            raise ValueError("Delivery item positions must be unique.")
        job_ids = [item.job_id for item in self.items]
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("Delivery item job identifiers must be unique.")
        referenced_paths: set[str] = set()
        for item in self.items:
            expected = (
                (item.output_path, item.output_sha256, DeliveryPayloadRole.OUTPUT_DOCUMENT),
                (item.audit_json_path, item.audit_json_sha256, DeliveryPayloadRole.AUDIT_JSON),
                (
                    item.audit_markdown_path,
                    item.audit_markdown_sha256,
                    DeliveryPayloadRole.AUDIT_MARKDOWN,
                ),
            )
            for path, digest, role in expected:
                payload = payload_by_path.get(path)
                if payload is None:
                    raise ValueError(f"Delivery item references a missing payload: {path}")
                if (
                    payload.job_id != item.job_id
                    or payload.role != role
                    or payload.sha256 != digest
                ):
                    raise ValueError(f"Delivery item evidence does not match payload: {path}")
                referenced_paths.add(path)
        if self.package_kind == DeliveryPackageKind.JOB:
            if len(self.items) != 1 or self.items[0].job_id != self.package_id:
                raise ValueError("A job delivery package must contain its single named job.")
            if self.batch_audit_path is not None:
                raise ValueError("A job delivery package cannot contain a batch audit.")
        else:
            if not self.batch_audit_path:
                raise ValueError("A batch delivery package must identify its batch audit.")
            batch_audit = payload_by_path.get(self.batch_audit_path)
            if batch_audit is None or batch_audit.role != DeliveryPayloadRole.BATCH_AUDIT:
                raise ValueError("The batch audit payload is missing or has the wrong role.")
            referenced_paths.add(self.batch_audit_path)
        if referenced_paths != set(payload_by_path):
            raise ValueError("Every payload file must be referenced by the delivery manifest.")
        return self


class DeliveryVerifiedItem(StrictModel):
    position: int = Field(ge=1)
    job_id: str
    source_filename: str
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_passed: bool
    content_integrity_passed: bool
    structure_review_items: int = Field(ge=0)
    delivery_review_items: int = Field(ge=0)
    source_review_features: int = Field(ge=0)


class DeliveryPackageVerification(StrictModel):
    schema_version: Literal["delivery-package-verification.v1"] = (
        "delivery-package-verification.v1"
    )
    valid: Literal[True] = True
    package_kind: DeliveryPackageKind
    package_id: str
    created_at: datetime
    application_version: str
    checksum_algorithm: Literal["sha256"] = "sha256"
    signature_status: DeliverySignatureStatus
    payload_file_count: int = Field(ge=1)
    payload_bytes: int = Field(ge=0)
    items: list[DeliveryVerifiedItem]
    warnings: list[str] = Field(default_factory=list)


def _validate_payload_path(path: str) -> None:
    if "\\" in path or "\x00" in path:
        raise ValueError("Delivery payload paths must use safe POSIX separators.")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Delivery payload paths cannot escape the package.")
    if not candidate.parts or candidate.parts[0] != "data" or len(candidate.parts) < 2:
        raise ValueError("Delivery payload paths must be below data/.")
