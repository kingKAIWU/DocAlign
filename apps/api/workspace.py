from __future__ import annotations

from typing import Any

from docalign_core.domain.batch import BatchStatus
from docalign_core.domain.enums import JobStatus
from docalign_core.domain.workspace import (
    StorageBatchItem,
    StorageDocumentItem,
    StoragePressure,
    StorageRecordCounts,
    WorkspaceStorageReport,
)
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from apps.api.batches import BatchService
from apps.api.db import (
    AnalysisRecord,
    BatchItemRecord,
    BatchRecord,
    Database,
    DocumentRecord,
    JobRecord,
    RulePackRecord,
    utcnow,
)
from apps.api.errors import ApiError
from apps.api.storage import LocalStorage

_TERMINAL_BATCHES = {
    BatchStatus.COMPLETED,
    BatchStatus.COMPLETED_WITH_ERRORS,
    BatchStatus.FAILED,
    BatchStatus.CANCELED,
}
_ACTIVE_JOBS = {
    JobStatus.QUEUED.value,
    JobStatus.ANALYZING.value,
    JobStatus.PLANNING.value,
    JobStatus.FORMATTING.value,
    JobStatus.VALIDATING.value,
    JobStatus.REPAIRING.value,
    JobStatus.CANCELING.value,
}


class WorkspaceService:
    """Read-only local data inventory for explicit, user-controlled cleanup."""

    def __init__(
        self,
        database: Database,
        storage: LocalStorage,
        batches: BatchService,
    ) -> None:
        self.database = database
        self.storage = storage
        self.batches = batches

    def storage_report(self, *, item_limit: int = 50) -> WorkspaceStorageReport:
        categories = self.storage.usage_categories()
        disk_total, disk_free = self.storage.disk_capacity()

        with self.database.session_factory() as session:
            document_count = self._count(session, DocumentRecord)
            analysis_count = self._count(session, AnalysisRecord)
            job_count = self._count(session, JobRecord)
            batch_count = self._count(session, BatchRecord)
            rule_pack_count = self._count(session, RulePackRecord)
            batch_ids = list(session.scalars(select(BatchRecord.id)))
            unbatched_documents = list(
                session.scalars(
                    select(DocumentRecord).where(
                        ~exists().where(
                            BatchItemRecord.document_id == DocumentRecord.id
                        )
                    )
                )
            )

        active_batches = 0
        terminal_batches: list[StorageBatchItem] = []
        reclaimable_bytes = 0
        for batch_id in batch_ids:
            try:
                audit = self.batches.get_batch(batch_id)
            except ApiError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if audit.status not in _TERMINAL_BATCHES:
                active_batches += 1
                continue
            batch_bytes = self._batch_artifact_bytes(batch_id)
            reclaimable_bytes += batch_bytes
            terminal_batches.append(
                StorageBatchItem(
                    batch_id=batch_id,
                    name=audit.name,
                    status=audit.status,
                    updated_at=audit.updated_at,
                    bytes=batch_bytes,
                    item_count=audit.summary.total,
                    completed=audit.summary.completed,
                    failed=audit.summary.failed,
                    canceled=audit.summary.canceled,
                )
            )

        document_items: list[StorageDocumentItem] = []
        for document in unbatched_documents:
            analysis_ids, jobs = self._document_relations(document.id)
            document_bytes = self.storage.document_artifact_bytes(
                document.id,
                analysis_ids,
                [job.id for job in jobs],
            )
            active_job_count = sum(job.status in _ACTIVE_JOBS for job in jobs)
            deletable = active_job_count == 0
            if deletable:
                reclaimable_bytes += document_bytes
            document_items.append(
                StorageDocumentItem(
                    document_id=document.id,
                    filename=document.original_filename,
                    created_at=document.created_at,
                    bytes=document_bytes,
                    analysis_count=len(analysis_ids),
                    job_count=len(jobs),
                    active_job_count=active_job_count,
                    deletable=deletable,
                )
            )

        terminal_batches.sort(key=lambda item: (item.bytes, item.updated_at), reverse=True)
        document_items.sort(key=lambda item: (item.bytes, item.created_at), reverse=True)
        docalign_bytes = sum(category.bytes for category in categories)
        return WorkspaceStorageReport(
            generated_at=utcnow(),
            docalign_bytes=docalign_bytes,
            reclaimable_bytes=min(reclaimable_bytes, docalign_bytes),
            disk_total_bytes=disk_total,
            disk_free_bytes=disk_free,
            pressure=_storage_pressure(disk_total, disk_free),
            categories=categories,
            records=StorageRecordCounts(
                documents=document_count,
                analyses=analysis_count,
                jobs=job_count,
                batches=batch_count,
                active_batches=active_batches,
                rule_packs=rule_pack_count,
            ),
            terminal_batches=terminal_batches[:item_limit],
            terminal_batches_truncated=len(terminal_batches) > item_limit,
            unbatched_documents=document_items[:item_limit],
            unbatched_documents_truncated=len(document_items) > item_limit,
        )

    def _batch_artifact_bytes(self, batch_id: str) -> int:
        with self.database.session_factory() as session:
            document_ids = list(
                session.scalars(
                    select(BatchItemRecord.document_id).where(
                        BatchItemRecord.batch_id == batch_id,
                        BatchItemRecord.document_id.is_not(None),
                    )
                )
            )
        artifacts = []
        for document_id in dict.fromkeys(document_ids):
            if document_id is None:
                continue
            analysis_ids, jobs = self._document_relations(document_id)
            artifacts.append(
                (document_id, analysis_ids, [job.id for job in jobs])
            )
        return self.storage.batch_artifact_bytes(batch_id, artifacts)

    def _document_relations(
        self, document_id: str
    ) -> tuple[list[str], list[JobRecord]]:
        with self.database.session_factory() as session:
            analysis_ids = list(
                session.scalars(
                    select(AnalysisRecord.id).where(
                        AnalysisRecord.document_id == document_id
                    )
                )
            )
            jobs = list(
                session.scalars(
                    select(JobRecord).where(JobRecord.document_id == document_id)
                )
            )
            for job in jobs:
                session.expunge(job)
        return analysis_ids, jobs

    @staticmethod
    def _count(session: Session, model: type[Any]) -> int:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _storage_pressure(total_bytes: int, free_bytes: int) -> StoragePressure:
    if total_bytes <= 0:
        return StoragePressure.NORMAL
    free_ratio = free_bytes / total_bytes
    if free_bytes < 1024**3 or free_ratio < 0.02:
        return StoragePressure.CRITICAL
    if free_bytes < 5 * 1024**3 or free_ratio < 0.10:
        return StoragePressure.WARNING
    return StoragePressure.NORMAL
