from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
import zipfile
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.docx.safety import DocxSafetyError
from docalign_core.domain.audit import ProcessingBoundaryAcknowledgmentMethod
from docalign_core.domain.batch import (
    BatchAudit,
    BatchAuditItem,
    BatchAuditSummary,
    BatchItemStatus,
    BatchStatus,
)
from docalign_core.domain.enums import AnalysisMode, JobStatus
from docalign_core.domain.formatting_spec import FormattingSpec
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.api.capacity import (
    WorkspaceCapacityGuard,
    is_capacity_error,
    package_working_bytes,
    upload_working_bytes,
)
from apps.api.db import (
    AnalysisRecord,
    BatchAttemptRecord,
    BatchItemRecord,
    BatchRecord,
    Database,
    DocumentRecord,
    JobRecord,
    as_utc,
    utcnow,
)
from apps.api.errors import ApiError
from apps.api.service import ApiService
from apps.api.storage import LocalStorage

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
logger = logging.getLogger(__name__)


class BatchService:
    """Durable orchestration for independent, retryable document jobs."""

    def __init__(
        self,
        core: ApiService,
        settings: Settings,
        database: Database,
        storage: LocalStorage,
        capacity: WorkspaceCapacityGuard,
    ) -> None:
        self.core = core
        self.settings = settings
        self.database = database
        self.storage = storage
        self.capacity = capacity

    async def create_batch(
        self,
        *,
        request_id: str,
        name: str,
        rule_pack_id: str,
        rule_pack_revision: int,
        processing_boundary_acknowledged: bool,
        files: list[UploadFile],
    ) -> tuple[BatchAudit, list[str]]:
        self._validate_request_id(request_id)
        if not processing_boundary_acknowledged:
            raise ApiError(
                409,
                "BATCH_PROCESSING_BOUNDARY_ACKNOWLEDGMENT_REQUIRED",
                "Confirm the batch complex-content review policy before processing.",
            )
        display_name = " ".join(name.split())
        if not display_name:
            raise ApiError(422, "BATCH_NAME_REQUIRED", "Batch name is required.")
        if len(display_name) > 160:
            raise ApiError(422, "BATCH_NAME_TOO_LONG", "Batch name is too long.")
        if not 1 <= len(files) <= self.settings.max_batch_files:
            raise ApiError(
                422,
                "BATCH_FILE_COUNT_INVALID",
                f"A batch must contain 1 to {self.settings.max_batch_files} files.",
            )
        self.capacity.ensure(
            sum(
                upload_working_bytes(
                    upload.size,
                    self.settings.max_upload_mb * 1024 * 1024,
                )
                for upload in files
            ),
            operation="batch_upload",
        )

        filenames = [self._filename(file) for file in files]
        manifest = json.dumps(filenames, ensure_ascii=False, separators=(",", ":"))
        artifact = self.core.get_rule_pack_artifact(rule_pack_id, rule_pack_revision)

        with self.database.session_factory() as session:
            existing = session.scalar(
                select(BatchRecord).where(BatchRecord.request_id == request_id)
            )
            if existing is not None:
                self._assert_same_batch_request(
                    existing,
                    name=display_name,
                    rule_pack_id=rule_pack_id,
                    rule_pack_revision=rule_pack_revision,
                    manifest=manifest,
                )
                batch_id = existing.id
            else:
                batch_id = f"batch_{uuid.uuid4().hex}"

        if existing is None:
            now = utcnow()
            try:
                with self.database.session_factory.begin() as session:
                    session.add(
                        BatchRecord(
                            id=batch_id,
                            request_id=request_id,
                            name=display_name,
                            rule_pack_id=rule_pack_id,
                            rule_pack_revision=rule_pack_revision,
                            rule_pack_name=artifact.name,
                            rule_pack_spec_sha256=artifact.spec_sha256,
                            item_count=len(files),
                            file_manifest_json=manifest,
                            processing_boundary_acknowledged=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.flush()
                    session.add_all(
                        BatchItemRecord(
                            id=f"batch_item_{uuid.uuid4().hex}",
                            batch_id=batch_id,
                            position=index,
                            original_filename=filename,
                            status=BatchItemStatus.PREPARING.value,
                            attempt_count=0,
                            created_at=now,
                            updated_at=now,
                        )
                        for index, filename in enumerate(filenames, start=1)
                    )
            except IntegrityError:
                with self.database.session_factory() as session:
                    concurrent = session.scalar(
                        select(BatchRecord).where(BatchRecord.request_id == request_id)
                    )
                    if concurrent is None:
                        raise
                    self._assert_same_batch_request(
                        concurrent,
                        name=display_name,
                        rule_pack_id=rule_pack_id,
                        rule_pack_revision=rule_pack_revision,
                        manifest=manifest,
                    )
                    batch_id = concurrent.id

        items = self._batch_items(batch_id)
        file_by_position = dict(enumerate(files, start=1))
        for item in items:
            if item.status == BatchItemStatus.PREPARING.value:
                await self._prepare_item(
                    item.id,
                    file_by_position[item.position],
                    artifact.spec,
                    request_id=self._initial_attempt_request_id(request_id, item.position),
                )
        return self.get_batch(batch_id), self.queued_job_ids(batch_id)

    def get_batch(self, batch_id: str) -> BatchAudit:
        with self.database.session_factory() as session:
            batch = session.get(BatchRecord, batch_id)
            if batch is None:
                raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
            items = list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.position)
                )
            )
            jobs = {
                item.current_job_id: session.get(JobRecord, item.current_job_id)
                for item in items
                if item.current_job_id
            }

        payload_items: list[BatchAuditItem] = []
        updated_at = batch.updated_at
        for item in items:
            job = jobs.get(item.current_job_id) if item.current_job_id else None
            payload = self._item_payload(item, job)
            payload_items.append(payload)
            updated_at = max(
                updated_at,
                item.updated_at,
                job.updated_at if job else item.updated_at,
            )

        completed = sum(item.status == BatchItemStatus.COMPLETED for item in payload_items)
        failed = sum(item.status == BatchItemStatus.FAILED for item in payload_items)
        canceled = sum(item.status == BatchItemStatus.CANCELED for item in payload_items)
        active = len(payload_items) - completed - failed - canceled
        if active:
            if batch.cancel_requested_at is not None or any(
                item.status == BatchItemStatus.CANCELING for item in payload_items
            ):
                status = BatchStatus.CANCELING
            else:
                status = (
                    BatchStatus.PREPARING
                    if any(item.status == BatchItemStatus.PREPARING for item in payload_items)
                    else BatchStatus.PROCESSING
                )
        elif canceled:
            status = BatchStatus.CANCELED
        elif completed == len(payload_items):
            status = BatchStatus.COMPLETED
        elif failed == len(payload_items):
            status = BatchStatus.FAILED
        else:
            status = BatchStatus.COMPLETED_WITH_ERRORS

        progress = round(sum(item.progress for item in payload_items) / len(payload_items))
        return BatchAudit(
            batch_id=batch.id,
            request_id=batch.request_id,
            name=batch.name,
            status=status,
            progress=progress,
            rule_pack_id=batch.rule_pack_id,
            rule_pack_revision=batch.rule_pack_revision,
            rule_pack_name=batch.rule_pack_name,
            rule_pack_spec_sha256=batch.rule_pack_spec_sha256,
            processing_boundary_acknowledged=batch.processing_boundary_acknowledged,
            summary=BatchAuditSummary(
                total=len(payload_items),
                completed=completed,
                failed=failed,
                canceled=canceled,
                active=active,
            ),
            items=payload_items,
            output_zip_url=(f"/api/v1/batches/{batch.id}/outputs.zip" if completed else None),
            delivery_package_url=(
                f"/api/v1/batches/{batch.id}/delivery-package.zip"
                if completed and not active
                else None
            ),
            audit_json_url=f"/api/v1/batches/{batch.id}/audit.json",
            created_at=as_utc(batch.created_at),
            updated_at=as_utc(updated_at),
        )

    async def retry_item(
        self, batch_id: str, item_id: str, request_id: str
    ) -> tuple[BatchAudit, list[str]]:
        self._validate_request_id(request_id)
        reserve_attempt = False
        with self.database.session_factory() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None or item.batch_id != batch_id:
                raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
            batch = session.get(BatchRecord, batch_id)
            if batch is None:
                raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
            if batch.cancel_requested_at is not None:
                raise ApiError(
                    409,
                    "BATCH_CANCELED",
                    "Canceled batches cannot accept new retry attempts.",
                )
            existing_attempt = session.scalar(
                select(BatchAttemptRecord).where(BatchAttemptRecord.request_id == request_id)
            )
            if existing_attempt is not None:
                if existing_attempt.batch_item_id != item_id:
                    raise ApiError(
                        409,
                        "IDEMPOTENCY_KEY_REUSED",
                        "This retry request ID belongs to another batch item.",
                    )
                if existing_attempt.job_id is not None:
                    return self.get_batch(batch_id), self.queued_job_ids(batch_id)
            job = session.get(JobRecord, item.current_job_id) if item.current_job_id else None
            current_status = (
                item.status
                if item.status == BatchItemStatus.PREPARING.value
                else job.status
                if job is not None
                else item.status
            )
            if current_status != BatchItemStatus.FAILED.value or not item.document_id:
                raise ApiError(
                    409,
                    "BATCH_ITEM_NOT_RETRYABLE",
                    "Only failed items with a stored source document can be retried.",
                )
            document_id = item.document_id
            analysis_id = item.analysis_id
            attempt_number = (
                existing_attempt.attempt_number
                if existing_attempt is not None
                else item.attempt_count + 1
            )
            reserve_attempt = existing_attempt is None
            pack_id = batch.rule_pack_id
            revision = batch.rule_pack_revision
            processing_boundary_acknowledged = batch.processing_boundary_acknowledged
            processing_boundary_acknowledged_at = batch.created_at

        if reserve_attempt:
            try:
                with self.database.session_factory.begin() as session:
                    session.add(
                        BatchAttemptRecord(
                            batch_item_id=item_id,
                            attempt_number=attempt_number,
                            request_id=request_id,
                            job_id=None,
                        )
                    )
                    current = session.get(BatchItemRecord, item_id)
                    if current is None:
                        raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
                    current.attempt_count = attempt_number
                    current.status = BatchItemStatus.PREPARING.value
                    current.error_code = None
                    current.error_message = None
                    current.updated_at = utcnow()
            except IntegrityError:
                with self.database.session_factory() as session:
                    concurrent = session.scalar(
                        select(BatchAttemptRecord).where(
                            BatchAttemptRecord.request_id == request_id
                        )
                    )
                    if concurrent is None or concurrent.batch_item_id != item_id:
                        raise ApiError(
                            409,
                            "BATCH_RETRY_IN_PROGRESS",
                            "Another retry for this file is already in progress.",
                        ) from None
                    if concurrent.job_id is not None:
                        return self.get_batch(batch_id), self.queued_job_ids(batch_id)
                    attempt_number = concurrent.attempt_number

        artifact = self.core.get_rule_pack_artifact(pack_id, revision)
        canceled_during_prepare = False
        try:
            if analysis_id is None:
                analysis_id, _ = await self.core.analyze(document_id, AnalysisMode.DETERMINISTIC)
            spec_id, _ = self.core.create_spec(artifact.spec, document_id)
            job_record = self.core.create_job(
                document_id,
                analysis_id,
                spec_id,
                processing_boundary_acknowledged=processing_boundary_acknowledged,
                acknowledgment_method=ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_BATCH,
                processing_boundary_acknowledged_at=processing_boundary_acknowledged_at,
            )
            with self.database.session_factory.begin() as session:
                current = session.get(BatchItemRecord, item_id)
                if current is None:
                    raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
                attempt = session.scalar(
                    select(BatchAttemptRecord).where(BatchAttemptRecord.request_id == request_id)
                )
                if attempt is None or attempt.batch_item_id != item_id:
                    raise ApiError(
                        500,
                        "BATCH_RETRY_RESERVATION_MISSING",
                        "The retry reservation could not be recovered.",
                    )
                attempt.job_id = job_record.id
                batch = session.get(BatchRecord, batch_id)
                if batch is None:
                    raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
                if batch.cancel_requested_at is not None:
                    queued_job = session.get(JobRecord, job_record.id)
                    if queued_job is not None:
                        queued_job.cancel_requested = True
                        queued_job.status = JobStatus.CANCELED.value
                        queued_job.progress = 100
                        queued_job.updated_at = utcnow()
                    current.status = BatchItemStatus.CANCELED.value
                    current.error_code = None
                    current.error_message = None
                    current.updated_at = utcnow()
                    canceled_during_prepare = True
                else:
                    current.status = BatchItemStatus.QUEUED.value
                current.analysis_id = analysis_id
                current.current_job_id = job_record.id
                current.attempt_count = attempt_number
                current.error_code = None
                current.error_message = None
                current.updated_at = utcnow()
        except (ApiError, DocxSafetyError) as exc:
            self._mark_failed(item_id, exc.code, exc.message)
            raise
        except Exception as exc:
            logger.exception("Failed to prepare retry for batch item %s", item_id)
            self._mark_failed(
                item_id,
                "BATCH_RETRY_PREPARATION_FAILED",
                "The retry could not be prepared. It is safe to try again.",
            )
            raise ApiError(
                500,
                "BATCH_RETRY_PREPARATION_FAILED",
                "The retry could not be prepared. It is safe to try again.",
            ) from exc
        return self.get_batch(batch_id), ([] if canceled_during_prepare else [job_record.id])

    def cancel_batch(self, batch_id: str) -> BatchAudit:
        terminal_jobs = {
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELED.value,
        }
        with self.database.session_factory.begin() as session:
            batch = session.get(BatchRecord, batch_id)
            if batch is None:
                raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
            items = list(
                session.scalars(select(BatchItemRecord).where(BatchItemRecord.batch_id == batch_id))
            )
            jobs = {
                item.current_job_id: session.get(JobRecord, item.current_job_id)
                for item in items
                if item.current_job_id
            }
            active = False
            for item in items:
                job = jobs.get(item.current_job_id) if item.current_job_id else None
                if item.status == BatchItemStatus.PREPARING.value:
                    active = True
                    item.status = BatchItemStatus.CANCELED.value
                    item.current_job_id = None
                    item.error_code = None
                    item.error_message = None
                    item.updated_at = utcnow()
                    continue
                if job is not None:
                    if job.status in terminal_jobs:
                        continue
                    active = True
                    job.cancel_requested = True
                    job.error_code = None
                    job.error_message = None
                    job.updated_at = utcnow()
                    if job.status == JobStatus.QUEUED.value:
                        job.status = JobStatus.CANCELED.value
                        job.progress = 100
                        item.status = BatchItemStatus.CANCELED.value
                    else:
                        job.status = JobStatus.CANCELING.value
                        item.status = BatchItemStatus.CANCELING.value
                    item.error_code = None
                    item.error_message = None
                    item.updated_at = utcnow()
                elif item.status not in {
                    BatchItemStatus.COMPLETED.value,
                    BatchItemStatus.FAILED.value,
                    BatchItemStatus.CANCELED.value,
                }:
                    active = True
                    item.status = BatchItemStatus.CANCELED.value
                    item.error_code = None
                    item.error_message = None
                    item.updated_at = utcnow()

            if not active and batch.cancel_requested_at is None:
                raise ApiError(
                    409,
                    "BATCH_NOT_ACTIVE",
                    "Only an active batch can be canceled.",
                )
            if batch.cancel_requested_at is None:
                batch.cancel_requested_at = utcnow()
            batch.updated_at = utcnow()
        return self.get_batch(batch_id)

    def delete_batch(self, batch_id: str) -> None:
        audit = self.get_batch(batch_id)
        if audit.status not in {
            BatchStatus.COMPLETED,
            BatchStatus.COMPLETED_WITH_ERRORS,
            BatchStatus.FAILED,
            BatchStatus.CANCELED,
        }:
            raise ApiError(
                409,
                "BATCH_NOT_TERMINAL",
                "Cancel the active batch and wait for it to stop before deleting it.",
            )

        with self.database.session_factory.begin() as session:
            batch = session.get(BatchRecord, batch_id)
            if batch is None:
                raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
            document_ids = list(
                session.scalars(
                    select(BatchItemRecord.document_id).where(
                        BatchItemRecord.batch_id == batch_id,
                        BatchItemRecord.document_id.is_not(None),
                    )
                )
            )
            artifacts: list[tuple[str, list[str], list[str]]] = []
            for document_id in document_ids:
                if document_id is None:
                    continue
                analysis_ids = list(
                    session.scalars(
                        select(AnalysisRecord.id).where(AnalysisRecord.document_id == document_id)
                    )
                )
                job_ids = list(
                    session.scalars(
                        select(JobRecord.id).where(JobRecord.document_id == document_id)
                    )
                )
                artifacts.append((document_id, analysis_ids, job_ids))
            session.delete(batch)
            for document_id, _, _ in artifacts:
                document = session.get(DocumentRecord, document_id)
                if document is not None:
                    session.delete(document)
            session.flush()
            for document_id, analysis_ids, job_ids in artifacts:
                self.storage.delete_document_artifacts(document_id, analysis_ids, job_ids)
            self.storage.delete_batch_artifacts(batch_id)

    def queued_job_ids(self, batch_id: str) -> list[str]:
        with self.database.session_factory() as session:
            rows = session.execute(
                select(BatchItemRecord.current_job_id, JobRecord.status)
                .join(JobRecord, JobRecord.id == BatchItemRecord.current_job_id)
                .where(BatchItemRecord.batch_id == batch_id)
            )
            return [job_id for job_id, status in rows if status == JobStatus.QUEUED.value]

    def build_output_zip(self, batch_id: str) -> Path:
        audit = self.get_batch(batch_id)
        completed = [item for item in audit.items if item.status == BatchItemStatus.COMPLETED]
        if not completed:
            raise ApiError(409, "BATCH_HAS_NO_OUTPUTS", "This batch has no completed documents.")
        output_bytes = 0
        for item in completed:
            if not item.job_id:
                continue
            job = self.core.get_job(item.job_id)
            if job.output_path and Path(job.output_path).is_file():
                output_bytes += Path(job.output_path).stat().st_size
        self.capacity.ensure(
            package_working_bytes(output_bytes),
            operation="batch_output_package",
        )
        target = self.storage.batch_output_zip_path(batch_id)
        temporary = target.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                archive.writestr(
                    "batch-audit.json",
                    audit.model_dump_json(indent=2),
                )
                for item in completed:
                    if not item.job_id:
                        raise ApiError(
                            409,
                            "BATCH_OUTPUT_MISSING",
                            "A completed item no longer references its processing job.",
                        )
                    job = self.core.get_job(item.job_id)
                    if not job.output_path or not Path(job.output_path).is_file():
                        raise ApiError(
                            409,
                            "BATCH_OUTPUT_MISSING",
                            "A completed output file is missing from local storage.",
                            {"item_id": item.item_id},
                        )
                    archive.write(
                        job.output_path,
                        arcname=f"{item.position:03d}_{self._safe_output_name(item.filename)}",
                    )
            os.replace(temporary, target)
        except OSError as exc:
            if is_capacity_error(exc):
                raise self.capacity.api_error(operation="batch_output_package") from exc
            raise
        finally:
            temporary.unlink(missing_ok=True)
        return target

    async def _prepare_item(
        self,
        item_id: str,
        upload: UploadFile,
        spec: FormattingSpec,
        *,
        request_id: str,
    ) -> None:
        try:
            with self.database.session_factory() as session:
                item = session.get(BatchItemRecord, item_id)
                if item is None:
                    raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
                batch = session.get(BatchRecord, item.batch_id)
                if batch is None:
                    raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
                document_id = item.document_id
                analysis_id = item.analysis_id
                processing_boundary_acknowledged = batch.processing_boundary_acknowledged
                processing_boundary_acknowledged_at = batch.created_at

            if document_id is None:
                document = await self.core.create_document(upload)
                document_id = str(document["document_id"])
                with self.database.session_factory.begin() as session:
                    current = session.get(BatchItemRecord, item_id)
                    if current is None:
                        raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
                    current.document_id = document_id
                    current.source_sha256 = str(document["sha256"])
                    current.updated_at = utcnow()
                self._enforce_batch_total(item_id, document_id)

            if analysis_id is None:
                analysis_id, _ = await self.core.analyze(document_id, AnalysisMode.DETERMINISTIC)
            spec_id, _ = self.core.create_spec(spec, document_id)
            job = self.core.create_job(
                document_id,
                analysis_id,
                spec_id,
                processing_boundary_acknowledged=processing_boundary_acknowledged,
                acknowledgment_method=ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_BATCH,
                processing_boundary_acknowledged_at=processing_boundary_acknowledged_at,
            )
            with self.database.session_factory.begin() as session:
                current = session.get(BatchItemRecord, item_id)
                if current is None:
                    raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
                batch = session.get(BatchRecord, current.batch_id)
                if batch is None:
                    raise ApiError(404, "BATCH_NOT_FOUND", "Batch not found.")
                current.analysis_id = analysis_id
                current.current_job_id = job.id
                current.attempt_count = 1
                if batch.cancel_requested_at is not None:
                    queued_job = session.get(JobRecord, job.id)
                    if queued_job is not None:
                        queued_job.cancel_requested = True
                        queued_job.status = JobStatus.CANCELED.value
                        queued_job.progress = 100
                        queued_job.updated_at = utcnow()
                    current.status = BatchItemStatus.CANCELED.value
                else:
                    current.status = BatchItemStatus.QUEUED.value
                current.error_code = None
                current.error_message = None
                current.updated_at = utcnow()
                session.add(
                    BatchAttemptRecord(
                        batch_item_id=item_id,
                        attempt_number=1,
                        request_id=request_id,
                        job_id=job.id,
                    )
                )
        except (ApiError, DocxSafetyError) as exc:
            self._mark_failed(item_id, exc.code, exc.message)
        except Exception:
            logger.exception("Failed to prepare batch item %s", item_id)
            self._mark_failed(
                item_id,
                "BATCH_ITEM_PREPARATION_FAILED",
                "The file could not be prepared for processing.",
            )

    def _enforce_batch_total(self, item_id: str, document_id: str) -> None:
        with self.database.session_factory() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is None:
                raise ApiError(404, "BATCH_ITEM_NOT_FOUND", "Batch item not found.")
            total = session.scalar(
                select(func.coalesce(func.sum(DocumentRecord.size_bytes), 0))
                .select_from(BatchItemRecord)
                .join(DocumentRecord, DocumentRecord.id == BatchItemRecord.document_id)
                .where(BatchItemRecord.batch_id == item.batch_id)
            )
        limit = self.settings.max_batch_total_mb * 1024 * 1024
        if int(total or 0) > limit:
            self.core.delete_document(document_id)
            raise ApiError(
                413,
                "BATCH_TOTAL_TOO_LARGE",
                "The total size of this batch exceeds the configured limit.",
                {"limit_bytes": limit},
            )

    def _item_payload(self, item: BatchItemRecord, job: JobRecord | None) -> BatchAuditItem:
        error_code: str | None
        error_message: str | None
        if item.status == BatchItemStatus.PREPARING.value:
            status = BatchItemStatus.PREPARING
            progress = 0
            error_code = None
            error_message = None
            result_summary = None
        elif item.current_job_id and job is None:
            status = BatchItemStatus.FAILED
            progress = 100
            error_code = "BATCH_JOB_MISSING"
            error_message = "The current processing job no longer exists."
            result_summary = None
        elif job is not None:
            job_payload = self.core.job_payload(job)
            status = BatchItemStatus(job.status)
            progress = (
                100
                if status in {BatchItemStatus.FAILED, BatchItemStatus.CANCELED}
                else job.progress
            )
            error_code = job.error_code
            error_message = job.error_message
            result_summary = job_payload.result_summary
        else:
            status = BatchItemStatus(item.status)
            progress = 100 if status in {BatchItemStatus.FAILED, BatchItemStatus.CANCELED} else 0
            error_code = item.error_code
            error_message = item.error_message
            result_summary = None
        return BatchAuditItem(
            item_id=item.id,
            position=item.position,
            filename=item.original_filename,
            status=status,
            progress=progress,
            source_sha256=item.source_sha256,
            document_id=item.document_id,
            analysis_id=item.analysis_id,
            job_id=item.current_job_id,
            attempt_count=item.attempt_count,
            retryable=status == BatchItemStatus.FAILED and item.document_id is not None,
            error_code=error_code,
            error_message=error_message,
            validation_passed=(
                result_summary.validation_passed if result_summary is not None else None
            ),
            content_integrity_passed=(
                result_summary.content_integrity_passed if result_summary is not None else None
            ),
            changed_mutations=(
                result_summary.changed_mutations if result_summary is not None else None
            ),
            source_review_features=(
                result_summary.source_review_features if result_summary is not None else None
            ),
            output_document_url=(
                f"/api/v1/jobs/{job.id}/output"
                if job is not None and status == BatchItemStatus.COMPLETED
                else None
            ),
            audit_json_url=(
                f"/api/v1/jobs/{job.id}/audit.json"
                if job is not None and job.audit_json_path
                else None
            ),
        )

    def _batch_items(self, batch_id: str) -> list[BatchItemRecord]:
        with self.database.session_factory() as session:
            items = list(
                session.scalars(
                    select(BatchItemRecord)
                    .where(BatchItemRecord.batch_id == batch_id)
                    .order_by(BatchItemRecord.position)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def _mark_failed(self, item_id: str, code: str, message: str) -> None:
        with self.database.session_factory.begin() as session:
            item = session.get(BatchItemRecord, item_id)
            if item is not None and item.status not in {
                BatchItemStatus.CANCELING.value,
                BatchItemStatus.CANCELED.value,
            }:
                item.status = BatchItemStatus.FAILED.value
                item.error_code = code
                item.error_message = message
                item.updated_at = utcnow()

    @staticmethod
    def _assert_same_batch_request(
        batch: BatchRecord,
        *,
        name: str,
        rule_pack_id: str,
        rule_pack_revision: int,
        manifest: str,
    ) -> None:
        if (
            batch.name != name
            or batch.rule_pack_id != rule_pack_id
            or batch.rule_pack_revision != rule_pack_revision
            or batch.file_manifest_json != manifest
        ):
            raise ApiError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "This batch request ID was already used for different inputs.",
            )

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ApiError(
                422,
                "INVALID_REQUEST_ID",
                "Request ID must contain 8 to 64 letters, digits, underscores, or hyphens.",
            )

    @staticmethod
    def _filename(upload: UploadFile) -> str:
        filename = Path(upload.filename or "document.docx").name.strip()
        return filename or "document.docx"

    @staticmethod
    def _initial_attempt_request_id(batch_request_id: str, position: int) -> str:
        digest = hashlib.sha256(f"{batch_request_id}:{position}".encode()).hexdigest()
        return f"initial_{digest[:32]}"

    @staticmethod
    def _safe_output_name(filename: str) -> str:
        stem = Path(filename).stem
        safe = "".join(character for character in stem if character not in '<>:"/\\|?*')
        safe = " ".join(safe.split()).strip(" .") or "document"
        return f"{safe[:120]}_formatted.docx"
