from __future__ import annotations

import errno
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from apps.api.errors import ApiError

MEBIBYTE = 1024**2
UPLOAD_METADATA_BYTES = MEBIBYTE
PROCESSING_METADATA_BYTES = 16 * MEBIBYTE
PACKAGE_METADATA_BYTES = 4 * MEBIBYTE
BACKUP_METADATA_BYTES = 64 * MEBIBYTE


@dataclass(frozen=True)
class CapacitySnapshot:
    total_bytes: int
    free_bytes: int
    reserve_bytes: int

    @property
    def write_headroom_bytes(self) -> int:
        return max(0, self.free_bytes - self.reserve_bytes)


class WorkspaceCapacityGuard:
    """Best-effort guard that preserves room for SQLite and atomic file publishing."""

    def __init__(self, root: Path, *, reserve_bytes: int) -> None:
        self.root = root.resolve()
        self.reserve_bytes = max(0, reserve_bytes)

    def snapshot(self, path: Path | None = None) -> CapacitySnapshot:
        usage = shutil.disk_usage(path or self.root)
        return CapacitySnapshot(
            total_bytes=usage.total,
            free_bytes=usage.free,
            reserve_bytes=self.reserve_bytes,
        )

    def ensure(
        self,
        required_bytes: int,
        *,
        operation: str,
        path: Path | None = None,
    ) -> CapacitySnapshot:
        required_bytes = max(0, required_bytes)
        snapshot = self.snapshot(path)
        if snapshot.write_headroom_bytes < required_bytes:
            raise self.api_error(
                operation=operation,
                required_bytes=required_bytes,
                snapshot=snapshot,
            )
        return snapshot

    def api_error(
        self,
        *,
        operation: str,
        required_bytes: int = 0,
        path: Path | None = None,
        snapshot: CapacitySnapshot | None = None,
    ) -> ApiError:
        try:
            current = snapshot or self.snapshot(path)
            details: dict[str, object] = {
                "operation": operation,
                "required_bytes": max(0, required_bytes),
                "reserve_bytes": current.reserve_bytes,
                "free_bytes": current.free_bytes,
                "write_headroom_bytes": current.write_headroom_bytes,
                "shortfall_bytes": max(0, max(0, required_bytes) - current.write_headroom_bytes),
            }
        except OSError:
            details = {
                "operation": operation,
                "required_bytes": max(0, required_bytes),
                "reserve_bytes": self.reserve_bytes,
            }
        return ApiError(
            507,
            "WORKSPACE_CAPACITY_INSUFFICIENT",
            "The local disk does not have enough safe working space for this operation.",
            details,
        )


def upload_working_bytes(size_hint: int | None, maximum_bytes: int) -> int:
    payload_bytes = maximum_bytes if size_hint is None else min(max(0, size_hint), maximum_bytes)
    return payload_bytes + UPLOAD_METADATA_BYTES


def processing_working_bytes(source_bytes: int) -> int:
    # Auto-layout, format, repair, and atomic publication can temporarily coexist.
    return max(0, source_bytes) * 6 + PROCESSING_METADATA_BYTES


def package_working_bytes(payload_bytes: int) -> int:
    # ZIP output can approach the uncompressed size for already-compressed DOCX content.
    return max(0, payload_bytes) + PACKAGE_METADATA_BYTES


def backup_working_bytes(workspace_bytes: int) -> int:
    # A normalized SQLite snapshot and the ZIP are both present during backup creation.
    return max(0, workspace_bytes) * 2 + BACKUP_METADATA_BYTES


def is_capacity_error(exc: BaseException) -> bool:
    capacity_errnos = {errno.ENOSPC}
    if hasattr(errno, "EDQUOT"):
        capacity_errnos.add(errno.EDQUOT)
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and (
            current.errno in capacity_errnos
            or getattr(current, "winerror", None)
            in {
                39,
                112,
            }
        ):
            return True
        if getattr(current, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            return True
        for related in (
            current.__cause__,
            current.__context__,
            getattr(current, "orig", None),
        ):
            if isinstance(related, BaseException):
                pending.append(related)
    return False
