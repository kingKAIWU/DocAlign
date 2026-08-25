from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRecord(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    source_sha256: Mapped[str] = mapped_column(String(64))
    result_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoleOverrideRecord(Base):
    __tablename__ = "role_overrides"
    __table_args__ = (UniqueConstraint("analysis_id", "node_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SpecRecord(Base):
    __tablename__ = "specs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64))
    json_payload: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    spec_id: Mapped[str] = mapped_column(ForeignKey("specs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_markdown_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _configure_sqlite)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def mark_interrupted_jobs(self) -> None:
        active = {"queued", "analyzing", "planning", "formatting", "validating", "repairing"}
        with self.session_factory.begin() as session:
            session.execute(
                update(JobRecord)
                .where(JobRecord.status.in_(active))
                .values(
                    status="failed",
                    progress=0,
                    error_code="JOB_INTERRUPTED",
                    error_message="The local service restarted before this job completed.",
                    updated_at=utcnow(),
                )
            )


def _configure_sqlite(dbapi_connection: object, _: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
