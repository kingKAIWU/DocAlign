from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import Field

from docalign_core.domain.base import StrictModel
from docalign_core.domain.document_ir import PackagePartIR

REQUIRED_DOCX_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
}


class SafetyLimits(StrictModel):
    max_file_bytes: int = 20 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_entries: int = 10_000
    max_compression_ratio: float = 100.0


class PackageInspection(StrictModel):
    parts: list[PackagePartIR] = Field(default_factory=list)
    image_hashes: list[str] = Field(default_factory=list)
    total_uncompressed_bytes: int = 0


class DocxSafetyError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_docx_package(
    path: Path,
    limits: SafetyLimits | None = None,
    *,
    require_docx_suffix: bool = True,
) -> PackageInspection:
    limits = limits or SafetyLimits()
    if not path.exists() or not path.is_file():
        raise DocxSafetyError("INVALID_DOCX", "The DOCX file does not exist.")
    if require_docx_suffix and path.suffix.lower() != ".docx":
        raise DocxSafetyError("UNSUPPORTED_FILE_TYPE", "Only .docx files are supported.")
    file_size = path.stat().st_size
    if file_size > limits.max_file_bytes:
        raise DocxSafetyError(
            "FILE_TOO_LARGE",
            "The DOCX file exceeds the configured upload limit.",
            {"size_bytes": file_size, "limit_bytes": limits.max_file_bytes},
        )
    if not zipfile.is_zipfile(path):
        raise DocxSafetyError("INVALID_DOCX", "The file is not a valid ZIP-based DOCX package.")

    parts: list[PackagePartIR] = []
    image_hashes: list[str] = []
    try:
        with zipfile.ZipFile(path) as package:
            infos = package.infolist()
            if len(infos) > limits.max_entries:
                raise DocxSafetyError(
                    "DOCX_ZIP_BOMB",
                    "The DOCX package contains too many entries.",
                    {"entries": len(infos), "limit": limits.max_entries},
                )
            names = {info.filename for info in infos}
            missing = sorted(REQUIRED_DOCX_PARTS - names)
            if missing:
                raise DocxSafetyError(
                    "DOCX_CORRUPTED",
                    "The DOCX package is missing required parts.",
                    {"missing_parts": missing},
                )
            total_uncompressed = 0
            for info in infos:
                _validate_entry_name(info.filename)
                if info.flag_bits & 0x1:
                    raise DocxSafetyError(
                        "DOCX_PASSWORD_PROTECTED",
                        "Encrypted DOCX packages are not supported.",
                        {"entry": info.filename},
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_uncompressed_bytes:
                    raise DocxSafetyError(
                        "DOCX_ZIP_BOMB",
                        "The DOCX uncompressed size exceeds the safety limit.",
                        {
                            "uncompressed_bytes": total_uncompressed,
                            "limit_bytes": limits.max_uncompressed_bytes,
                        },
                    )
                ratio = _compression_ratio(info)
                if ratio > limits.max_compression_ratio:
                    raise DocxSafetyError(
                        "DOCX_ZIP_BOMB",
                        "A DOCX package entry exceeds the compression-ratio limit.",
                        {"entry": info.filename, "ratio": ratio},
                    )
                if info.is_dir():
                    continue
                data = package.read(info)
                digest = hashlib.sha256(data).hexdigest()
                parts.append(
                    PackagePartIR(
                        path=info.filename,
                        compressed_size=info.compress_size,
                        uncompressed_size=info.file_size,
                        sha256=digest,
                    )
                )
                if info.filename.startswith("word/media/"):
                    image_hashes.append(digest)
    except zipfile.BadZipFile as exc:
        raise DocxSafetyError("DOCX_CORRUPTED", "The DOCX ZIP package is corrupted.") from exc

    return PackageInspection(
        parts=sorted(parts, key=lambda item: item.path),
        image_hashes=sorted(image_hashes),
        total_uncompressed_bytes=total_uncompressed,
    )


def _validate_entry_name(name: str) -> None:
    if "\\" in name or "\x00" in name:
        raise DocxSafetyError(
            "DOCX_PATH_TRAVERSAL",
            "The DOCX package contains an unsafe entry path.",
            {"entry": name},
        )
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DocxSafetyError(
            "DOCX_PATH_TRAVERSAL",
            "The DOCX package contains an unsafe entry path.",
            {"entry": name},
        )


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 1.0
    if info.compress_size == 0:
        return float("inf")
    return info.file_size / info.compress_size
