from __future__ import annotations

import tempfile
from pathlib import Path

from docalign_core import __version__
from docalign_core.config import Settings
from docalign_core.delivery import (
    DeliveryPackageError,
    DeliveryPackageLimits,
    audit_content_integrity_passed,
    audit_delivery_review_items,
    build_delivery_package,
    verify_delivery_package,
)
from docalign_core.docx.safety import sha256_file
from docalign_core.domain.audit import AuditReport
from docalign_core.domain.batch import BatchStatus
from docalign_core.domain.delivery import (
    DeliveryPackageItem,
    DeliveryPackageKind,
    DeliveryPackageManifest,
    DeliveryPackageVerification,
    DeliveryPayloadFile,
    DeliveryPayloadRole,
)
from docalign_core.domain.enums import JobStatus
from fastapi import UploadFile
from pydantic import ValidationError

from apps.api.batches import BatchService
from apps.api.db import JobRecord, as_utc
from apps.api.errors import ApiError
from apps.api.service import ApiService
from apps.api.storage import LocalStorage


class DeliveryService:
    def __init__(
        self,
        core: ApiService,
        batches: BatchService,
        settings: Settings,
        storage: LocalStorage,
    ) -> None:
        self.core = core
        self.batches = batches
        self.settings = settings
        self.storage = storage
        self.limits = DeliveryPackageLimits(
            max_file_bytes=(settings.max_batch_total_mb + 20) * 1024 * 1024,
            max_uncompressed_bytes=(settings.max_batch_total_mb + 40) * 1024 * 1024,
            max_entries=settings.max_zip_entries,
            max_compression_ratio=settings.max_compression_ratio,
        )

    def build_job_package(self, job_id: str) -> Path:
        job = self.core.get_job(job_id)
        if job.status != JobStatus.COMPLETED.value:
            raise ApiError(
                409,
                "JOB_NOT_COMPLETED",
                "A delivery package is available only after the job completes.",
            )
        document = self.core.get_document(job.document_id)
        audit = self._read_job_audit(job)
        payloads, files, item = self._job_payload(
            job,
            audit,
            position=1,
            source_filename=document.original_filename,
        )
        created_at = as_utc(job.updated_at)
        manifest = DeliveryPackageManifest(
            package_kind=DeliveryPackageKind.JOB,
            package_id=job.id,
            created_at=created_at,
            application_version=__version__,
            payload_files=files,
            items=[item],
        )
        target = self.storage.job_delivery_package_path(job.id)
        build_delivery_package(target, manifest, payloads)
        verify_delivery_package(target, self.limits)
        return target

    def build_batch_package(self, batch_id: str) -> Path:
        batch = self.batches.get_batch(batch_id)
        if batch.status in {
            BatchStatus.PREPARING,
            BatchStatus.PROCESSING,
            BatchStatus.CANCELING,
        }:
            raise ApiError(
                409,
                "BATCH_NOT_TERMINAL",
                "Wait for the batch to finish before creating its delivery package.",
            )
        completed = [item for item in batch.items if item.status.value == "completed"]
        if not completed:
            raise ApiError(
                409,
                "BATCH_HAS_NO_OUTPUTS",
                "This batch has no completed documents to deliver.",
            )

        payloads: dict[str, Path | bytes] = {}
        files: list[DeliveryPayloadFile] = []
        package_items: list[DeliveryPackageItem] = []
        for batch_item in completed:
            if not batch_item.job_id:
                raise ApiError(
                    409,
                    "BATCH_OUTPUT_MISSING",
                    "A completed batch item no longer identifies its processing job.",
                    {"item_id": batch_item.item_id},
                )
            job = self.core.get_job(batch_item.job_id)
            audit = self._read_job_audit(job)
            item_payloads, item_files, package_item = self._job_payload(
                job,
                audit,
                position=batch_item.position,
                source_filename=batch_item.filename,
                item_id=batch_item.item_id,
            )
            payloads.update(item_payloads)
            files.extend(item_files)
            package_items.append(package_item)

        batch_audit_path = "data/batch-audit.json"
        batch_audit_bytes = (batch.model_dump_json(indent=2) + "\n").encode()
        payloads[batch_audit_path] = batch_audit_bytes
        files.append(
            DeliveryPayloadFile(
                path=batch_audit_path,
                role=DeliveryPayloadRole.BATCH_AUDIT,
                sha256=_sha256_bytes(batch_audit_bytes),
                size_bytes=len(batch_audit_bytes),
            )
        )
        manifest = DeliveryPackageManifest(
            package_kind=DeliveryPackageKind.BATCH,
            package_id=batch.batch_id,
            created_at=batch.updated_at,
            application_version=__version__,
            payload_files=files,
            items=package_items,
            batch_audit_path=batch_audit_path,
        )
        target = self.storage.batch_delivery_package_path(batch.batch_id)
        build_delivery_package(target, manifest, payloads)
        verify_delivery_package(target, self.limits)
        return target

    async def verify_upload(self, upload: UploadFile) -> DeliveryPackageVerification:
        filename = (upload.filename or "delivery-package.zip").replace("\\", "/").split("/")[-1]
        if Path(filename).suffix.casefold() != ".zip":
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_UNSUPPORTED_FILE",
                "Only .zip delivery packages are supported.",
            )
        with tempfile.TemporaryDirectory(prefix="docalign-delivery-verify-") as directory:
            target = Path(directory) / "delivery-package.zip"
            size = 0
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.limits.max_file_bytes:
                        raise DeliveryPackageError(
                            "DELIVERY_PACKAGE_TOO_LARGE",
                            "The delivery package exceeds the configured size limit.",
                            {"limit_bytes": self.limits.max_file_bytes},
                        )
                    output.write(chunk)
            if not size:
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_INVALID", "The delivery package is empty."
                )
            return verify_delivery_package(target, self.limits)

    def _job_payload(
        self,
        job: JobRecord,
        audit: AuditReport,
        *,
        position: int,
        source_filename: str,
        item_id: str | None = None,
    ) -> tuple[dict[str, Path | bytes], list[DeliveryPayloadFile], DeliveryPackageItem]:
        if (
            not job.output_path
            or not Path(job.output_path).is_file()
            or not job.audit_json_path
            or not Path(job.audit_json_path).is_file()
            or not job.audit_markdown_path
            or not Path(job.audit_markdown_path).is_file()
        ):
            raise ApiError(
                409,
                "DELIVERY_PACKAGE_ARTIFACT_MISSING",
                "A completed job is missing an output or audit artifact.",
                {"job_id": job.id},
            )
        output = Path(job.output_path)
        audit_json = Path(job.audit_json_path)
        audit_markdown = Path(job.audit_markdown_path)
        prefix = f"{position:03d}"
        output_path = f"data/outputs/{prefix}_formatted.docx"
        audit_json_path = f"data/audits/{prefix}_audit.json"
        audit_markdown_path = f"data/audits/{prefix}_audit.md"
        output_sha256 = sha256_file(output)
        audit_json_sha256 = sha256_file(audit_json)
        audit_markdown_sha256 = sha256_file(audit_markdown)
        if (
            audit.job_id != job.id
            or audit.output_sha256 != output_sha256
        ):
            raise ApiError(
                409,
                "DELIVERY_PACKAGE_EVIDENCE_MISMATCH",
                "The output and audit evidence do not belong to the same completed job.",
                {"job_id": job.id},
            )
        boundary = audit.source_processing_boundary
        item = DeliveryPackageItem(
            position=position,
            item_id=item_id,
            job_id=job.id,
            source_filename=source_filename,
            source_sha256=audit.source_sha256,
            output_path=output_path,
            output_sha256=output_sha256,
            audit_json_path=audit_json_path,
            audit_json_sha256=audit_json_sha256,
            audit_markdown_path=audit_markdown_path,
            audit_markdown_sha256=audit_markdown_sha256,
            validation_passed=audit.validation.valid,
            content_integrity_passed=audit_content_integrity_passed(audit),
            structure_review_items=audit.summary.unknown_blocks,
            delivery_review_items=audit_delivery_review_items(audit),
            source_review_features=boundary.review_feature_count if boundary else 0,
        )
        payloads: dict[str, Path | bytes] = {
            output_path: output,
            audit_json_path: audit_json,
            audit_markdown_path: audit_markdown,
        }
        files = [
            DeliveryPayloadFile(
                path=output_path,
                role=DeliveryPayloadRole.OUTPUT_DOCUMENT,
                sha256=output_sha256,
                size_bytes=output.stat().st_size,
                job_id=job.id,
            ),
            DeliveryPayloadFile(
                path=audit_json_path,
                role=DeliveryPayloadRole.AUDIT_JSON,
                sha256=audit_json_sha256,
                size_bytes=audit_json.stat().st_size,
                job_id=job.id,
            ),
            DeliveryPayloadFile(
                path=audit_markdown_path,
                role=DeliveryPayloadRole.AUDIT_MARKDOWN,
                sha256=audit_markdown_sha256,
                size_bytes=audit_markdown.stat().st_size,
                job_id=job.id,
            ),
        ]
        return payloads, files, item

    @staticmethod
    def _read_job_audit(job: JobRecord) -> AuditReport:
        if not job.audit_json_path or not Path(job.audit_json_path).is_file():
            raise ApiError(
                409,
                "DELIVERY_PACKAGE_ARTIFACT_MISSING",
                "The completed job audit is missing.",
                {"job_id": job.id},
            )
        try:
            return AuditReport.model_validate_json(
                Path(job.audit_json_path).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError) as exc:
            raise ApiError(
                409,
                "DELIVERY_PACKAGE_AUDIT_INVALID",
                "The completed job audit cannot be validated.",
                {"job_id": job.id},
            ) from exc


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
