from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docalign_core.docx.safety import DocxSafetyError, SafetyLimits, validate_docx_package


def test_valid_docx_package_is_inspected(academic_docx: Path) -> None:
    result = validate_docx_package(academic_docx)
    assert result.parts
    assert len(result.image_hashes) == 1


def test_non_docx_suffix_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.zip"
    path.write_bytes(b"not-a-docx")
    with pytest.raises(DocxSafetyError) as captured:
        validate_docx_package(path)
    assert captured.value.code == "UNSUPPORTED_FILE_TYPE"


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("_rels/.rels", "<Relationships/>")
        package.writestr("word/document.xml", "<document/>")
        package.writestr("../escape", "bad")
    with pytest.raises(DocxSafetyError) as captured:
        validate_docx_package(path)
    assert captured.value.code == "DOCX_PATH_TRAVERSAL"


def test_missing_file_size_limit_and_missing_parts_are_rejected(
    academic_docx: Path, tmp_path: Path
) -> None:
    with pytest.raises(DocxSafetyError) as missing_file:
        validate_docx_package(tmp_path / "missing.docx")
    assert missing_file.value.code == "INVALID_DOCX"

    with pytest.raises(DocxSafetyError) as too_large:
        validate_docx_package(academic_docx, SafetyLimits(max_file_bytes=1))
    assert too_large.value.code == "FILE_TOO_LARGE"

    incomplete = tmp_path / "incomplete.docx"
    with zipfile.ZipFile(incomplete, "w") as package:
        package.writestr("word/document.xml", "<document/>")
    with pytest.raises(DocxSafetyError) as missing_parts:
        validate_docx_package(incomplete)
    assert missing_parts.value.code == "DOCX_CORRUPTED"
    assert "[Content_Types].xml" in missing_parts.value.details["missing_parts"]


def test_entry_count_uncompressed_size_and_ratio_limits(tmp_path: Path) -> None:
    many = tmp_path / "many.docx"
    _write_minimal_package(many, {"word/extra.xml": b"x"})
    with pytest.raises(DocxSafetyError) as entry_count:
        validate_docx_package(many, SafetyLimits(max_entries=3))
    assert entry_count.value.code == "DOCX_ZIP_BOMB"

    expanded = tmp_path / "expanded.docx"
    _write_minimal_package(expanded, {"word/extra.xml": b"x" * 100})
    with pytest.raises(DocxSafetyError) as uncompressed:
        validate_docx_package(expanded, SafetyLimits(max_uncompressed_bytes=10))
    assert uncompressed.value.code == "DOCX_ZIP_BOMB"

    compressed = tmp_path / "compressed.docx"
    _write_minimal_package(
        compressed,
        {"word/extra.bin": b"0" * 20_000},
        compression=zipfile.ZIP_DEFLATED,
    )
    with pytest.raises(DocxSafetyError) as ratio:
        validate_docx_package(compressed, SafetyLimits(max_compression_ratio=2))
    assert ratio.value.code == "DOCX_ZIP_BOMB"


def test_backslash_and_corrupt_zip_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unsafe = tmp_path / "backslash.docx"
    _write_minimal_package(unsafe, {"word\\escape.xml": b"bad"})
    with pytest.raises(DocxSafetyError) as backslash:
        validate_docx_package(unsafe)
    assert backslash.value.code == "DOCX_PATH_TRAVERSAL"

    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"PK fake")
    monkeypatch.setattr(zipfile, "is_zipfile", lambda _: True)

    class BrokenZipFile:
        def __init__(self, _: Path) -> None:
            raise zipfile.BadZipFile("broken")

    monkeypatch.setattr(zipfile, "ZipFile", BrokenZipFile)
    with pytest.raises(DocxSafetyError) as broken:
        validate_docx_package(corrupt)
    assert broken.value.code == "DOCX_CORRUPTED"


def _write_minimal_package(
    path: Path,
    additions: dict[str, bytes],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("_rels/.rels", "<Relationships/>")
        package.writestr("word/document.xml", "<document/>")
        for name, data in additions.items():
            package.writestr(name, data)
