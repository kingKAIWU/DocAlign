from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docalign_core.delivery import (
    DeliveryPackageError,
    DeliveryPackageLimits,
    verify_delivery_package,
)


def test_delivery_verifier_rejects_path_traversal_before_reading_payload(tmp_path: Path) -> None:
    package = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")

    with pytest.raises(DeliveryPackageError) as raised:
        verify_delivery_package(package)

    assert raised.value.code == "DELIVERY_PACKAGE_UNSAFE"


def test_delivery_verifier_enforces_file_size_before_opening_zip(tmp_path: Path) -> None:
    package = tmp_path / "large.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("placeholder.txt", "payload")

    with pytest.raises(DeliveryPackageError) as raised:
        verify_delivery_package(
            package,
            DeliveryPackageLimits(
                max_file_bytes=1,
                max_uncompressed_bytes=1024,
                max_entries=10,
                max_compression_ratio=100,
            ),
        )

    assert raised.value.code == "DELIVERY_PACKAGE_TOO_LARGE"
