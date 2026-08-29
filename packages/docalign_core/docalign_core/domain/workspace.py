from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.batch import BatchStatus


class StorageCategoryId(StrEnum):
    SOURCE_DOCUMENTS = "source_documents"
    ANALYSES = "analyses"
    JOB_AUDITS = "job_audits"
    OUTPUTS = "outputs"
    BATCH_PACKAGES = "batch_packages"
    DATABASE = "database"
    OTHER = "other"


class StoragePressure(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class StorageCategory(StrictModel):
    category: StorageCategoryId
    bytes: int = Field(ge=0)
    file_count: int = Field(ge=0)


class StorageRecordCounts(StrictModel):
    documents: int = Field(ge=0)
    analyses: int = Field(ge=0)
    jobs: int = Field(ge=0)
    batches: int = Field(ge=0)
    active_batches: int = Field(ge=0)
    rule_packs: int = Field(ge=0)


class StorageDocumentItem(StrictModel):
    document_id: str
    filename: str
    created_at: datetime
    bytes: int = Field(ge=0)
    analysis_count: int = Field(ge=0)
    job_count: int = Field(ge=0)
    active_job_count: int = Field(ge=0)
    deletable: bool


class StorageBatchItem(StrictModel):
    batch_id: str
    name: str
    status: BatchStatus
    updated_at: datetime
    bytes: int = Field(ge=0)
    item_count: int = Field(ge=1)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    canceled: int = Field(ge=0)


class WorkspaceStorageReport(StrictModel):
    schema_version: Literal["workspace-storage.v1"] = "workspace-storage.v1"
    generated_at: datetime
    docalign_bytes: int = Field(ge=0)
    reclaimable_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    pressure: StoragePressure
    categories: list[StorageCategory]
    records: StorageRecordCounts
    terminal_batches: list[StorageBatchItem]
    terminal_batches_truncated: bool
    unbatched_documents: list[StorageDocumentItem]
    unbatched_documents_truncated: bool
