from __future__ import annotations

import os
import platform
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from docalign_core import __version__
from docalign_core.config import Settings
from docalign_core.domain.diagnostics import (
    DiagnosticCheck,
    DiagnosticCheckStatus,
    DiagnosticConfiguration,
    DiagnosticDataSummary,
    DiagnosticErrorCodeCount,
    DiagnosticOverall,
    DiagnosticRuntime,
    SupportDiagnosticReport,
)
from docalign_core.domain.enums import JobStatus
from docalign_core.domain.workspace import StoragePressure
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from apps.api.db import (
    AnalysisRecord,
    BatchRecord,
    Database,
    DocumentRecord,
    JobRecord,
    RulePackRecord,
    utcnow,
)
from apps.api.migrations import database_revisions
from apps.api.storage import LocalStorage, storage_pressure

_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.ANALYZING.value,
    JobStatus.PLANNING.value,
    JobStatus.FORMATTING.value,
    JobStatus.VALIDATING.value,
    JobStatus.REPAIRING.value,
    JobStatus.CANCELING.value,
}
_SAFE_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
_EXCLUDED_DATA = [
    "document_content",
    "filenames",
    "record_identifiers",
    "local_paths",
    "database_connection_string",
    "model_endpoint",
    "credentials",
    "raw_logs",
]


class DiagnosticService:
    """Build a support report that is useful without containing document data."""

    def __init__(
        self,
        settings: Settings,
        database: Database | None,
        storage: LocalStorage,
    ) -> None:
        self.settings = settings
        self.database = database
        self.storage = storage

    def report(self) -> SupportDiagnosticReport:
        checks: list[DiagnosticCheck] = []
        counts: dict[str, int] = {
            "documents": 0,
            "analyses": 0,
            "jobs": 0,
            "active_jobs": 0,
            "failed_jobs": 0,
            "batches": 0,
            "rule_packs": 0,
        }
        error_codes: list[DiagnosticErrorCodeCount] = []
        artifact_paths: list[Path | None] = []

        database_available = False
        if self.database is None:
            checks.append(
                _check(
                    "database_connection",
                    DiagnosticCheckStatus.FAIL,
                    "本地数据库",
                    "数据库文件尚不存在或无法安全打开。",
                    "先完成安装；如原有工作区突然出现此提示，请保留数据目录并寻求支持。",
                )
            )
        else:
            try:
                with self.database.session_factory() as session:
                    session.scalar(select(1))
                    counts["documents"] = _count(session, DocumentRecord)
                    counts["analyses"] = _count(session, AnalysisRecord)
                    counts["jobs"] = _count(session, JobRecord)
                    counts["batches"] = _count(session, BatchRecord)
                    counts["rule_packs"] = _count(session, RulePackRecord)
                    job_statuses: dict[str, int] = {
                        row[0]: int(row[1])
                        for row in session.execute(
                            select(JobRecord.status, func.count()).group_by(JobRecord.status)
                        )
                    }
                    active_jobs = 0
                    for status in _ACTIVE_JOB_STATUSES:
                        active_jobs += job_statuses.get(status, 0)
                    counts["active_jobs"] = active_jobs
                    counts["failed_jobs"] = job_statuses.get(JobStatus.FAILED.value, 0)
                    error_codes = _recent_error_codes(session)
                    artifact_paths = [
                        Path(value) for value in session.scalars(select(DocumentRecord.stored_path))
                    ]
                    artifact_paths.extend(
                        Path(value) for value in session.scalars(select(AnalysisRecord.result_path))
                    )
                    completed_jobs = list(
                        session.scalars(
                            select(JobRecord).where(JobRecord.status == JobStatus.COMPLETED.value)
                        )
                    )
                    for job in completed_jobs:
                        for value in (
                            job.output_path,
                            job.audit_json_path,
                            job.audit_markdown_path,
                        ):
                            artifact_paths.append(Path(value) if value else None)
                database_available = True
                checks.append(
                    _check(
                        "database_connection",
                        DiagnosticCheckStatus.PASS,
                        "本地数据库",
                        "连接正常，基础查询已通过。",
                    )
                )
            except Exception:  # A diagnostic report must survive database driver failures.
                checks.append(
                    _check(
                        "database_connection",
                        DiagnosticCheckStatus.FAIL,
                        "本地数据库",
                        "数据库连接或基础查询失败。诊断报告未收集异常原文。",
                        "关闭重复启动的 DocAlign 进程后重试；仍失败时保留数据目录并寻求支持。",
                    )
                )

        checks.append(self._schema_check(database_available))
        storage_values = self._storage_check(checks)
        checks.append(_artifact_check(artifact_paths, database_available, self.storage.root))
        checks.append(self._model_check())

        overall = DiagnosticOverall.READY
        if any(check.status == DiagnosticCheckStatus.FAIL for check in checks):
            overall = DiagnosticOverall.ACTION_REQUIRED
        elif any(check.status == DiagnosticCheckStatus.WARNING for check in checks):
            overall = DiagnosticOverall.ATTENTION

        return SupportDiagnosticReport(
            generated_at=utcnow(),
            overall=overall,
            runtime=DiagnosticRuntime(
                application_version=__version__,
                python_version=platform.python_version(),
                operating_system=platform.system() or "Unknown",
                operating_system_release=platform.release() or "Unknown",
                architecture=platform.machine() or "Unknown",
            ),
            configuration=DiagnosticConfiguration(
                database_backend=_database_backend(self.settings.database_url),
                llm_configured=self.settings.llm_configured,
                job_concurrency=self.settings.job_concurrency,
                max_upload_mb=self.settings.max_upload_mb,
                max_batch_files=self.settings.max_batch_files,
                max_batch_total_mb=self.settings.max_batch_total_mb,
                min_free_mb=self.settings.min_free_mb,
            ),
            data_summary=DiagnosticDataSummary(**storage_values, **counts),
            checks=checks,
            recent_error_codes=error_codes,
            excluded_data=_EXCLUDED_DATA,
        )

    def _schema_check(self, database_available: bool) -> DiagnosticCheck:
        if not database_available or self.database is None:
            return _check(
                "database_schema",
                DiagnosticCheckStatus.FAIL,
                "数据库版本",
                "由于数据库不可用，无法核对迁移版本。",
                "先解决本地数据库问题，再重新运行诊断。",
            )
        try:
            current, expected = database_revisions(self.database)
        except Exception:  # Do not leak migration or connection details.
            return _check(
                "database_schema",
                DiagnosticCheckStatus.FAIL,
                "数据库版本",
                "无法读取数据库迁移版本。",
                "重新启动 DocAlign 以执行自动升级；仍失败时不要手工删除数据库。",
            )
        if current == expected and current:
            return _check(
                "database_schema",
                DiagnosticCheckStatus.PASS,
                "数据库版本",
                "数据库结构与当前应用版本一致。",
            )
        return _check(
            "database_schema",
            DiagnosticCheckStatus.FAIL,
            "数据库版本",
            "数据库结构与当前应用版本不一致。",
            "重新启动 DocAlign 以执行自动升级；升级失败时先备份数据目录。",
        )

    def _storage_check(self, checks: list[DiagnosticCheck]) -> dict[str, int | StoragePressure]:
        values: dict[str, int | StoragePressure] = {
            "docalign_bytes": 0,
            "disk_total_bytes": 0,
            "disk_free_bytes": 0,
            "storage_pressure": StoragePressure.NORMAL,
        }
        root = self.storage.root
        if not root.exists() or not root.is_dir():
            checks.append(
                _check(
                    "data_directory",
                    DiagnosticCheckStatus.FAIL,
                    "本地数据目录",
                    "数据目录不存在或不是文件夹。",
                    "先完成安装；如果目录被移动，请恢复原位置或更新管理员配置。",
                )
            )
            checks.append(
                _check(
                    "disk_capacity",
                    DiagnosticCheckStatus.FAIL,
                    "磁盘空间",
                    "由于数据目录不可用，无法读取磁盘容量。",
                    "先解决本地数据目录问题。",
                )
            )
            return values

        writable = os.access(root, os.W_OK | os.X_OK)
        checks.append(
            _check(
                "data_directory",
                DiagnosticCheckStatus.PASS if writable else DiagnosticCheckStatus.FAIL,
                "本地数据目录",
                (
                    "目录存在且当前进程可以写入。"
                    if writable
                    else "目录存在，但当前进程没有写入权限。"
                ),
                None if writable else "检查目录权限和磁盘只读状态，然后重新启动 DocAlign。",
            )
        )
        try:
            categories = self.storage.usage_categories()
            total_bytes, free_bytes = self.storage.disk_capacity()
            reserve_bytes = self.settings.min_free_mb * 1024 * 1024
            pressure = storage_pressure(
                total_bytes,
                free_bytes,
                minimum_free_bytes=reserve_bytes,
            )
            values.update(
                docalign_bytes=sum(category.bytes for category in categories),
                disk_total_bytes=total_bytes,
                disk_free_bytes=free_bytes,
                storage_pressure=pressure,
            )
            if free_bytes <= reserve_bytes:
                checks.append(
                    _check(
                        "disk_capacity",
                        DiagnosticCheckStatus.FAIL,
                        "磁盘空间",
                        "磁盘可用空间已进入 DocAlign 安全保留范围。",
                        "先下载需保留的成果，再从设置页清理不需要的终态数据。",
                    )
                )
            elif pressure in {StoragePressure.WARNING, StoragePressure.CRITICAL}:
                checks.append(
                    _check(
                        "disk_capacity",
                        DiagnosticCheckStatus.WARNING,
                        "磁盘空间",
                        "磁盘可用空间偏低。",
                        "检查设置页的分类占用，并提前备份和清理不再需要的数据。",
                    )
                )
            else:
                checks.append(
                    _check(
                        "disk_capacity",
                        DiagnosticCheckStatus.PASS,
                        "磁盘空间",
                        "当前可用空间充足。",
                    )
                )
        except OSError:
            checks.append(
                _check(
                    "disk_capacity",
                    DiagnosticCheckStatus.FAIL,
                    "磁盘空间",
                    "读取磁盘容量或 DocAlign 占用失败。",
                    "检查磁盘是否在线、数据目录权限是否正常。",
                )
            )
        return values

    def _model_check(self) -> DiagnosticCheck:
        if self.settings.llm_configured:
            return _check(
                "optional_model",
                DiagnosticCheckStatus.PASS,
                "可选兼容模型",
                "模型端点与模型名称已配置；诊断报告不会包含端点或密钥。",
            )
        return _check(
            "optional_model",
            DiagnosticCheckStatus.PASS,
            "可选兼容模型",
            "未配置。默认整理、确定性分析、体检和排版仍可正常使用。",
        )


def _artifact_check(
    paths: list[Path | None], database_available: bool, data_root: Path
) -> DiagnosticCheck:
    if not database_available:
        return _check(
            "artifact_references",
            DiagnosticCheckStatus.FAIL,
            "本地产物引用",
            "由于数据库不可用，无法核对本地产物。",
            "先解决本地数据库问题。",
        )
    missing = 0
    resolved_root = data_root.resolve()
    for path in paths:
        if path is None:
            missing += 1
            continue
        try:
            resolved = path.resolve(strict=False)
            if resolved != resolved_root and resolved_root not in resolved.parents:
                missing += 1
                continue
            if not path.is_file():
                missing += 1
        except OSError:
            missing += 1
    if missing:
        return _check(
            "artifact_references",
            DiagnosticCheckStatus.WARNING,
            "本地产物引用",
            f"发现 {missing} 个数据库记录指向的本地产物缺失。未收集文件名或路径。",
            "保留本诊断 JSON；可清理对应旧工作区，或在需要恢复数据时寻求支持。",
        )
    return _check(
        "artifact_references",
        DiagnosticCheckStatus.PASS,
        "本地产物引用",
        f"已核对 {len(paths)} 个本地产物引用，未发现缺失。",
    )


def _recent_error_codes(session: Session) -> list[DiagnosticErrorCodeCount]:
    cutoff = utcnow() - timedelta(days=30)
    rows = session.execute(
        select(JobRecord.error_code, func.count())
        .where(JobRecord.error_code.is_not(None), JobRecord.updated_at >= cutoff)
        .group_by(JobRecord.error_code)
    ).all()
    aggregated: Counter[str] = Counter()
    for code, count in rows:
        safe_code = (
            code if isinstance(code, str) and _SAFE_ERROR_CODE.fullmatch(code) else "UNKNOWN_ERROR"
        )
        aggregated[safe_code] += int(count)
    return [
        DiagnosticErrorCodeCount(code=code, count=count)
        for code, count in sorted(aggregated.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]


def _count(session: Session, model: type[Any]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _database_backend(database_url: str) -> str:
    try:
        return make_url(database_url).get_backend_name()
    except Exception:
        return "unknown"


def _check(
    check_id: str,
    status: DiagnosticCheckStatus,
    title: str,
    detail: str,
    remediation: str | None = None,
) -> DiagnosticCheck:
    return DiagnosticCheck(
        check_id=check_id,
        status=status,
        title=title,
        detail=detail,
        remediation=remediation,
    )


def database_exists_without_connecting(settings: Settings) -> bool:
    """Avoid creating an empty SQLite file during standalone diagnostics."""

    try:
        url = make_url(settings.database_url)
    except Exception:
        return False
    if url.get_backend_name() != "sqlite":
        return False
    if not url.database or url.database == ":memory:":
        return True
    return Path(url.database).expanduser().exists()


def standalone_diagnostic_service(settings: Settings) -> DiagnosticService:
    database = (
        Database(settings.database_url) if database_exists_without_connecting(settings) else None
    )
    storage = LocalStorage(settings.data_dir, create=False)
    return DiagnosticService(settings, database, storage)
