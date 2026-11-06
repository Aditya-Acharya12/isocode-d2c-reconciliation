from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_column
from app.models.enums import FileType, IssueSeverity, JobKind, JobStatus, MappingSource


class UploadedFile(CreatedAtMixin, Base):
    """Comes from a partner, or for orders from a sales channel, but never both."""

    __tablename__ = "uploaded_files"
    __table_args__ = (
        UniqueConstraint(
            "partner_id",
            "channel_id",
            "file_type",
            "sha256",
            name="uq_uploaded_files_source_content",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("num_nonnulls(partner_id, channel_id) = 1", name="one_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("sales_channels.id"))
    file_type: Mapped[FileType] = mapped_column(
        enum_column(FileType, "uploaded_file_type"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Structure found by the analysis job: columns, sample rows and value profiles.
    detection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class UploadedFileBlob(Base):
    """File contents, kept apart so that listing files never loads them.

    Stored in Postgres because Render's filesystem is wiped on every restart.
    """

    __tablename__ = "uploaded_file_blobs"

    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"), primary_key=True
    )
    content_gzip: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ColumnMapping(TimestampMixin, Base):
    __tablename__ = "column_mappings"
    __table_args__ = (
        UniqueConstraint(
            "partner_id",
            "channel_id",
            "file_type",
            name="uq_column_mappings_source_type",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("num_nonnulls(partner_id, channel_id) = 1", name="one_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("sales_channels.id"))
    file_type: Mapped[FileType] = mapped_column(
        enum_column(FileType, "column_mapping_file_type"), nullable=False
    )
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("mapping_versions.id", use_alter=True, name="fk_column_mappings_active_version")
    )


class MappingVersion(CreatedAtMixin, Base):
    """Immutable. Restoring an old version creates a new copy, keeping history linear."""

    __tablename__ = "mapping_versions"
    __table_args__ = (UniqueConstraint("mapping_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_id: Mapped[int] = mapped_column(ForeignKey("column_mappings.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    header_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[MappingSource] = mapped_column(
        enum_column(MappingSource, "mapping_source"), nullable=False
    )
    # What was suggested, kept to show how a person's approval differed from it.
    suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BackgroundJob(TimestampMixin, Base):
    """The persisted job queue, so that work survives a server restart.

    A running job with a stale heartbeat was interrupted. Each job commits its
    business effects in a single transaction, so an interrupted job left nothing
    behind and can run again. idempotency_key stops the same work being queued twice.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        Index(
            "ix_background_jobs_claimable",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[JobKind] = mapped_column(enum_column(JobKind, "job_kind"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class ImportJob(TimestampMixin, Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id"), unique=True, nullable=False
    )
    mapping_version_id: Mapped[int] = mapped_column(
        ForeignKey("mapping_versions.id"), nullable=False
    )
    background_job_id: Mapped[int] = mapped_column(
        ForeignKey("background_jobs.id"), unique=True, nullable=False
    )
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_inserted: Mapped[int | None] = mapped_column(Integer)
    rows_updated: Mapped[int | None] = mapped_column(Integer)
    rows_duplicate: Mapped[int | None] = mapped_column(Integer)
    rows_rejected: Mapped[int | None] = mapped_column(Integer)
    rows_excluded: Mapped[int | None] = mapped_column(Integer)
    rows_warned: Mapped[int | None] = mapped_column(Integer)
    # Counts by issue type, and whether stored issues hit the storage cap.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    timings_ms: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ImportRowError(CreatedAtMixin, Base):
    """Every issue found in a row, whether or not the row was imported."""

    __tablename__ = "import_row_errors"
    __table_args__ = (Index("ix_import_row_errors_job_row", "import_job_id", "row_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    import_job_id: Mapped[int] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[IssueSeverity] = mapped_column(
        enum_column(IssueSeverity, "import_issue_severity"),
        default=IssueSeverity.ERROR,
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(Text)
    raw_value: Mapped[str | None] = mapped_column(Text)
