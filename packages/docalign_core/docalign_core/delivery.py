from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from docalign_core.docx.safety import DocxSafetyError, SafetyLimits, validate_docx_package
from docalign_core.domain.audit import CONTENT_INTEGRITY_CODES, AuditReport
from docalign_core.domain.base import StrictModel
from docalign_core.domain.batch import BatchAudit, BatchItemStatus
from docalign_core.domain.delivery import (
    DeliveryPackageKind,
    DeliveryPackageManifest,
    DeliveryPackageVerification,
    DeliveryVerifiedItem,
)

BAGIT_DECLARATION = b"BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
DELIVERY_MANIFEST_PATH = "delivery-manifest.json"
PAYLOAD_MANIFEST_PATH = "manifest-sha256.txt"
TAG_MANIFEST_PATH = "tagmanifest-sha256.txt"
BAG_INFO_PATH = "bag-info.txt"
README_PATH = "README.txt"
DELIVERY_README = (
    "DocAlign 可校验交付包\n\n"
    "- data/outputs/：格式化 DOCX\n"
    "- data/audits/：每份输出对应的 JSON 与 Markdown 审计\n"
    "- delivery-manifest.json：交付项、来源摘要和验证结论\n"
    "- manifest-sha256.txt：全部载荷文件的 SHA-256\n"
    "- tagmanifest-sha256.txt：全部说明与清单文件的 SHA-256\n\n"
    "可使用 `docalign verify-delivery <package.zip>` 重新校验。\n"
    "本包未使用发布者数字签名；摘要可检测内容变化，但不能证明发布者身份。\n"
).encode()
_REQUIRED_TAGS = {
    "bagit.txt",
    BAG_INFO_PATH,
    README_PATH,
    DELIVERY_MANIFEST_PATH,
    PAYLOAD_MANIFEST_PATH,
    TAG_MANIFEST_PATH,
}
_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._/-]+)$")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class DeliveryPackageLimits(StrictModel):
    max_file_bytes: int = 220 * 1024 * 1024
    max_uncompressed_bytes: int = 240 * 1024 * 1024
    max_entries: int = 10_000
    max_compression_ratio: float = 100.0


class DeliveryPackageError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def build_delivery_package(
    target: Path,
    manifest: DeliveryPackageManifest,
    payloads: Mapping[str, Path | bytes],
) -> Path:
    """Create a deterministic BagIt 1.0 ZIP for a verified DocAlign delivery."""

    expected = {item.path for item in manifest.payload_files}
    if set(payloads) != expected:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_PAYLOAD_MISMATCH",
            "The delivery payload does not match its manifest.",
        )
    _validate_bag_info_value(manifest.package_id)
    _validate_bag_info_value(manifest.application_version)
    for entry in manifest.payload_files:
        source = payloads[entry.path]
        actual_size, actual_sha256 = _source_evidence(source)
        if actual_size != entry.size_bytes or actual_sha256 != entry.sha256:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_PAYLOAD_MISMATCH",
                "A delivery payload changed before packaging.",
                {"path": entry.path},
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
    ) as holder:
        temporary = Path(holder.name)
    manifest_bytes = (
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    payload_manifest_bytes = _checksum_manifest(
        {item.path: item.sha256 for item in manifest.payload_files}
    )
    payload_bytes = sum(item.size_bytes for item in manifest.payload_files)
    bag_info_bytes = (
        f"Bagging-Date: {manifest.created_at.date().isoformat()}\n"
        f"Bag-Software-Agent: DocAlign {manifest.application_version}\n"
        f"External-Identifier: {manifest.package_id}\n"
        f"Payload-Oxum: {payload_bytes}.{len(manifest.payload_files)}\n"
    ).encode()
    tags = {
        "bagit.txt": BAGIT_DECLARATION,
        BAG_INFO_PATH: bag_info_bytes,
        README_PATH: DELIVERY_README,
        DELIVERY_MANIFEST_PATH: manifest_bytes,
        PAYLOAD_MANIFEST_PATH: payload_manifest_bytes,
    }
    tag_manifest_bytes = _checksum_manifest(
        {name: hashlib.sha256(data).hexdigest() for name, data in tags.items()}
    )
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name in (
                "bagit.txt",
                BAG_INFO_PATH,
                README_PATH,
                DELIVERY_MANIFEST_PATH,
                PAYLOAD_MANIFEST_PATH,
            ):
                _write_zip_entry(archive, name, tags[name])
            _write_zip_entry(archive, TAG_MANIFEST_PATH, tag_manifest_bytes)
            for path in sorted(payloads):
                _write_zip_entry(archive, path, payloads[path])
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def verify_delivery_package(
    path: Path,
    limits: DeliveryPackageLimits | None = None,
) -> DeliveryPackageVerification:
    limits = limits or DeliveryPackageLimits()
    if not path.is_file():
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_NOT_FOUND", "The delivery package does not exist."
        )
    if path.suffix.casefold() != ".zip":
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_UNSUPPORTED_FILE", "Only .zip delivery packages are supported."
        )
    if path.stat().st_size > limits.max_file_bytes:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_TOO_LARGE",
            "The delivery package exceeds the configured size limit.",
            {"limit_bytes": limits.max_file_bytes},
        )
    if not zipfile.is_zipfile(path):
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "The file is not a valid ZIP package."
        )

    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            _validate_zip_entries(infos, limits)
            files = {info.filename: info for info in infos if not info.is_dir()}
            missing = sorted(_REQUIRED_TAGS - set(files))
            unexpected_tags = sorted(
                name
                for name in files
                if name not in _REQUIRED_TAGS and not name.startswith("data/")
            )
            if missing or unexpected_tags:
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_INCOMPLETE",
                    "The delivery package is missing required files or contains unknown tags.",
                    {"missing": missing, "unexpected_tags": unexpected_tags},
                )
            if _read_small_entry(package, "bagit.txt") != BAGIT_DECLARATION:
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_INVALID",
                    "The BagIt declaration is invalid or unsupported.",
                )
            payload_manifest = _parse_checksum_manifest(
                _read_small_entry(package, PAYLOAD_MANIFEST_PATH), is_payload=True
            )
            tag_manifest = _parse_checksum_manifest(
                _read_small_entry(package, TAG_MANIFEST_PATH), is_payload=False
            )
            payload_paths = {name for name in files if name.startswith("data/")}
            if set(payload_manifest) != payload_paths:
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_INCOMPLETE",
                    "The payload manifest does not list every payload exactly once.",
                )
            expected_tags = _REQUIRED_TAGS - {TAG_MANIFEST_PATH}
            if set(tag_manifest) != expected_tags:
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_INCOMPLETE",
                    "The tag manifest does not list every required tag exactly once.",
                )
            computed_payload = {
                name: _hash_zip_entry(package, files[name]) for name in sorted(payload_paths)
            }
            computed_tags = {
                name: hashlib.sha256(_read_small_entry(package, name)).hexdigest()
                for name in sorted(expected_tags)
            }
            _assert_checksums(payload_manifest, computed_payload, "payload")
            _assert_checksums(tag_manifest, computed_tags, "tag")
            manifest = _read_delivery_manifest(package)
            _verify_bag_info(package, manifest)
            _verify_delivery_manifest_files(manifest, files, computed_payload)
            _verify_delivery_audits(package, manifest)
            _verify_output_documents(package, manifest, limits)
            if manifest.package_kind == DeliveryPackageKind.BATCH:
                _verify_batch_audit(package, manifest)
            return DeliveryPackageVerification(
                package_kind=manifest.package_kind,
                package_id=manifest.package_id,
                created_at=manifest.created_at,
                application_version=manifest.application_version,
                checksum_algorithm=manifest.checksum_algorithm,
                signature_status=manifest.signature_status,
                payload_file_count=len(manifest.payload_files),
                payload_bytes=sum(item.size_bytes for item in manifest.payload_files),
                items=[
                    DeliveryVerifiedItem(
                        position=item.position,
                        job_id=item.job_id,
                        source_filename=item.source_filename,
                        output_sha256=item.output_sha256,
                        validation_passed=item.validation_passed,
                        content_integrity_passed=item.content_integrity_passed,
                        structure_review_items=item.structure_review_items,
                        delivery_review_items=item.delivery_review_items,
                        source_review_features=item.source_review_features,
                    )
                    for item in manifest.items
                ],
                warnings=[
                    "SHA-256 verifies package integrity but does not prove the publisher identity; "
                    "this package is not digitally signed."
                ],
            )
    except zipfile.BadZipFile as exc:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "The delivery ZIP package is corrupted."
        ) from exc


def audit_delivery_review_items(audit: AuditReport) -> int:
    applied_preset = audit.execution_evidence.applied_preset if audit.execution_evidence else None
    if not applied_preset:
        return 0
    return (
        len(applied_preset.review_requirements)
        + len(applied_preset.acceptance_manual_checks)
        + (0 if applied_preset.matches_catalog_spec else 1)
    )


def audit_content_integrity_passed(audit: AuditReport) -> bool:
    return not any(issue.code in CONTENT_INTEGRITY_CODES for issue in audit.validation.issues)


def _source_evidence(source: Path | bytes) -> tuple[int, str]:
    if isinstance(source, bytes):
        return len(source), hashlib.sha256(source).hexdigest()
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _checksum_manifest(entries: Mapping[str, str]) -> bytes:
    return "".join(f"{entries[path]}  {path}\n" for path in sorted(entries)).encode("utf-8")


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    source: Path | bytes,
) -> None:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with archive.open(info, "w", force_zip64=True) as output:
        if isinstance(source, bytes):
            output.write(source)
        else:
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)


def _validate_zip_entries(
    infos: list[zipfile.ZipInfo], limits: DeliveryPackageLimits
) -> None:
    if len(infos) > limits.max_entries:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_UNSAFE",
            "The delivery package contains too many entries.",
            {"limit": limits.max_entries},
        )
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_UNSAFE", "The delivery package contains duplicate paths."
        )
    total_uncompressed = 0
    for info in infos:
        _validate_zip_path(info.filename)
        if info.flag_bits & 0x1:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_UNSAFE", "Encrypted delivery packages are not supported."
            )
        if stat.S_ISLNK(info.external_attr >> 16):
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_UNSAFE", "Delivery packages cannot contain symbolic links."
            )
        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_uncompressed_bytes:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_UNSAFE",
                "The delivery package expands beyond the configured limit.",
                {"limit_bytes": limits.max_uncompressed_bytes},
            )
        ratio = _compression_ratio(info)
        if ratio > limits.max_compression_ratio:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_UNSAFE",
                "A delivery entry exceeds the compression-ratio limit.",
                {"entry": info.filename, "ratio": ratio},
            )


def _validate_zip_path(name: str) -> None:
    if "\\" in name or "\x00" in name:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_UNSAFE", "The delivery package contains an unsafe path."
        )
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_UNSAFE", "The delivery package contains an unsafe path."
        )


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 1.0
    if info.compress_size == 0:
        return float("inf")
    return info.file_size / info.compress_size


def _read_small_entry(package: zipfile.ZipFile, name: str) -> bytes:
    info = package.getinfo(name)
    if info.file_size > 5 * 1024 * 1024:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "A delivery metadata file is unexpectedly large."
        )
    return package.read(info)


def _parse_checksum_manifest(data: bytes, *, is_payload: bool) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "A checksum manifest is not valid UTF-8."
        ) from exc
    if text.startswith("\ufeff"):
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "Checksum manifests cannot contain a UTF-8 BOM."
        )
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = _MANIFEST_LINE.fullmatch(line)
        if not match:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_INVALID", "A checksum manifest line is invalid."
            )
        digest, path = match.groups()
        if path in result:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_INVALID", "A checksum manifest contains a duplicate path."
            )
        if is_payload != path.startswith("data/"):
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_INVALID", "A checksum manifest references the wrong scope."
            )
        result[path] = digest
    if not result:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "A checksum manifest cannot be empty."
        )
    return result


def _hash_zip_entry(package: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with package.open(info) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_checksums(
    expected: Mapping[str, str], actual: Mapping[str, str], scope: str
) -> None:
    mismatched = [path for path in sorted(expected) if expected[path] != actual.get(path)]
    if mismatched:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INTEGRITY_FAILED",
            f"The {scope} checksum validation failed.",
            {"paths": mismatched[:20]},
        )


def _read_delivery_manifest(package: zipfile.ZipFile) -> DeliveryPackageManifest:
    try:
        return DeliveryPackageManifest.model_validate_json(
            _read_small_entry(package, DELIVERY_MANIFEST_PATH)
        )
    except ValidationError as exc:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_MANIFEST_INVALID",
            "The package metadata does not match delivery-package.v1.",
            {"errors": _safe_validation_errors(exc)},
        ) from exc


def _verify_delivery_manifest_files(
    manifest: DeliveryPackageManifest,
    infos: Mapping[str, zipfile.ZipInfo],
    computed: Mapping[str, str],
) -> None:
    for item in manifest.payload_files:
        info = infos.get(item.path)
        if (
            info is None
            or info.file_size != item.size_bytes
            or computed.get(item.path) != item.sha256
        ):
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_MANIFEST_MISMATCH",
                "The delivery metadata does not match a payload file.",
                {"path": item.path},
            )


def _verify_delivery_audits(
    package: zipfile.ZipFile,
    manifest: DeliveryPackageManifest,
) -> None:
    for item in manifest.items:
        try:
            audit = AuditReport.model_validate_json(package.read(item.audit_json_path))
        except (KeyError, ValidationError) as exc:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_AUDIT_INVALID",
                "A job audit in the delivery package is invalid.",
                {"job_id": item.job_id},
            ) from exc
        try:
            markdown = package.read(item.audit_markdown_path)
        except KeyError as exc:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_AUDIT_INVALID",
                "A Markdown audit in the delivery package is missing.",
                {"job_id": item.job_id},
            ) from exc
        if markdown != audit.to_markdown().encode():
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_AUDIT_MISMATCH",
                "The Markdown audit does not match its JSON audit.",
                {"job_id": item.job_id},
            )
        boundary = audit.source_processing_boundary
        expected = {
            "job_id": audit.job_id,
            "source_sha256": audit.source_sha256,
            "output_sha256": audit.output_sha256,
            "validation_passed": audit.validation.valid,
            "content_integrity_passed": audit_content_integrity_passed(audit),
            "structure_review_items": audit.summary.unknown_blocks,
            "delivery_review_items": audit_delivery_review_items(audit),
            "source_review_features": boundary.review_feature_count if boundary else 0,
        }
        actual = {
            "job_id": item.job_id,
            "source_sha256": item.source_sha256,
            "output_sha256": item.output_sha256,
            "validation_passed": item.validation_passed,
            "content_integrity_passed": item.content_integrity_passed,
            "structure_review_items": item.structure_review_items,
            "delivery_review_items": item.delivery_review_items,
            "source_review_features": item.source_review_features,
        }
        if expected != actual:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_AUDIT_MISMATCH",
                "A job audit does not match the delivery metadata.",
                {"job_id": item.job_id},
            )


def _verify_batch_audit(
    package: zipfile.ZipFile,
    manifest: DeliveryPackageManifest,
) -> None:
    if not manifest.batch_audit_path:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_AUDIT_INVALID", "The batch audit path is missing."
        )
    try:
        audit = BatchAudit.model_validate_json(package.read(manifest.batch_audit_path))
    except (KeyError, ValidationError) as exc:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_AUDIT_INVALID", "The batch audit is invalid."
        ) from exc
    completed_jobs = {
        item.job_id
        for item in audit.items
        if item.status == BatchItemStatus.COMPLETED and item.job_id is not None
    }
    if audit.batch_id != manifest.package_id or completed_jobs != {
        item.job_id for item in manifest.items
    }:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_AUDIT_MISMATCH",
            "The batch audit does not match the packaged completed jobs.",
        )


def _verify_output_documents(
    package: zipfile.ZipFile,
    manifest: DeliveryPackageManifest,
    limits: DeliveryPackageLimits,
) -> None:
    safety_limits = SafetyLimits(
        max_file_bytes=limits.max_uncompressed_bytes,
        max_uncompressed_bytes=limits.max_uncompressed_bytes,
        max_entries=limits.max_entries,
        max_compression_ratio=limits.max_compression_ratio,
    )
    with tempfile.TemporaryDirectory(prefix="docalign-delivery-docx-") as directory:
        root = Path(directory)
        for item in manifest.items:
            target = root / f"{item.position:03d}.docx"
            try:
                with package.open(item.output_path) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                validate_docx_package(target, safety_limits)
            except (KeyError, DocxSafetyError, OSError) as exc:
                code = exc.code if isinstance(exc, DocxSafetyError) else "OUTPUT_UNREADABLE"
                raise DeliveryPackageError(
                    "DELIVERY_PACKAGE_OUTPUT_INVALID",
                    "A packaged output is not a safe, valid DOCX file.",
                    {"job_id": item.job_id, "reason": code},
                ) from exc


def _verify_bag_info(
    package: zipfile.ZipFile,
    manifest: DeliveryPackageManifest,
) -> None:
    try:
        text = _read_small_entry(package, BAG_INFO_PATH).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_INVALID", "The BagIt metadata is not valid UTF-8."
        ) from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ": " not in line:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_INVALID", "The BagIt metadata contains an invalid line."
            )
        key, value = line.split(": ", 1)
        if key in fields:
            raise DeliveryPackageError(
                "DELIVERY_PACKAGE_INVALID", "The BagIt metadata contains a duplicate field."
            )
        fields[key] = value
    expected_oxum = (
        f"{sum(item.size_bytes for item in manifest.payload_files)}."
        f"{len(manifest.payload_files)}"
    )
    if (
        fields.get("External-Identifier") != manifest.package_id
        or fields.get("Bagging-Date") != manifest.created_at.date().isoformat()
        or fields.get("Bag-Software-Agent") != f"DocAlign {manifest.application_version}"
        or fields.get("Payload-Oxum") != expected_oxum
    ):
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_MANIFEST_MISMATCH",
            "The BagIt metadata does not match the delivery manifest.",
        )


def _safe_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "location": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_input=False, include_url=False)[:20]
    ]


def _validate_bag_info_value(value: str) -> None:
    if "\r" in value or "\n" in value:
        raise DeliveryPackageError(
            "DELIVERY_PACKAGE_MANIFEST_INVALID", "Bag metadata cannot contain line breaks."
        )
