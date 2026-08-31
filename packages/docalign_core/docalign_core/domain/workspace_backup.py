from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel


class WorkspaceBackupFileRole(StrEnum):
    DATABASE = "database"
    SOURCE_DOCUMENT = "source_document"
    ANALYSIS = "analysis"
    JOB_ARTIFACT = "job_artifact"
    OUTPUT = "output"
    BATCH_ARTIFACT = "batch_artifact"


class WorkspaceBackupFile(StrictModel):
    archive_path: str
    restore_path: str
    role: WorkspaceBackupFileRole
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceBackupManifest(StrictModel):
    schema_version: Literal["workspace-backup.v1"] = "workspace-backup.v1"
    backup_id: str = Field(pattern=r"^backup_[0-9a-f]{32}$")
    created_at: datetime
    application_version: str
    database_revision: str
    checksum_algorithm: Literal["sha256"] = "sha256"
    signature_status: Literal["not_signed"] = "not_signed"
    encryption_status: Literal["not_encrypted"] = "not_encrypted"
    database_archive_path: str
    files: list[WorkspaceBackupFile] = Field(min_length=1)
    excluded_runtime_data: list[str]


class WorkspaceBackupVerification(StrictModel):
    schema_version: Literal["workspace-backup-verification.v1"] = "workspace-backup-verification.v1"
    valid: Literal[True] = True
    backup_id: str
    created_at: datetime
    application_version: str
    database_revision: str
    checksum_algorithm: Literal["sha256"] = "sha256"
    signature_status: Literal["not_signed"] = "not_signed"
    encryption_status: Literal["not_encrypted"] = "not_encrypted"
    file_count: int = Field(ge=1)
    payload_bytes: int = Field(ge=0)
    source_document_count: int = Field(ge=0)
    warnings: list[str]


class WorkspaceRestoreReceipt(StrictModel):
    schema_version: Literal["workspace-restore.v1"] = "workspace-restore.v1"
    backup_id: str
    restored_at: datetime
    database_filename: str
    file_count: int = Field(ge=1)
    payload_bytes: int = Field(ge=0)
    source_document_count: int = Field(ge=0)
