from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FileType, IssueSeverity, JobKind, JobStatus, MappingSource
from app.services.ingestion.spec import MappingSpec

FileStage = Literal[
    "analysing", "analysis_failed", "needs_mapping", "importing", "import_failed", "imported"
]


class ImportCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobStatus
    rows_total: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_duplicate: int | None
    rows_rejected: int | None
    rows_excluded: int | None
    rows_warned: int | None
    error: str | None = None
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FileOut(BaseModel):
    id: int
    filename: str
    file_type: FileType
    partner_id: int | None
    channel_id: int | None
    source_name: str | None
    size_bytes: int
    sha256: str
    uploaded_by: str
    uploaded_at: datetime
    is_synthetic: bool
    stage: FileStage
    error: str | None
    import_job: ImportCounts | None


class FileDetail(FileOut):
    detection: dict[str, Any] | None


class FilePage(BaseModel):
    items: list[FileOut]
    total: int
    limit: int
    offset: int


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: JobKind
    status: JobStatus
    attempts: int
    max_attempts: int
    error: str | None
    result: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class ValidationRequest(BaseModel):
    spec: MappingSpec


class ImportRequest(BaseModel):
    spec: MappingSpec
    validation_job_id: int
    # A person must see and accept how many rows won't be imported.
    acknowledged_invalid_rows: int = Field(default=0, ge=0)
    suggested_by: Literal["gemini", "heuristic", "saved", "manual"] = "manual"
    suggestion: dict[str, Any] | None = None


class ImportDetail(ImportCounts):
    file: FileOut
    mapping_version: int
    summary: dict[str, Any] | None
    timings_ms: dict[str, Any] | None


class RowIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: str | None
    code: str
    severity: IssueSeverity
    message: str | None
    raw_value: str | None


class RowIssuePage(BaseModel):
    items: list[RowIssueOut]
    total: int
    limit: int
    offset: int


class MappingVersionOut(BaseModel):
    id: int
    version: int
    is_active: bool
    source: MappingSource
    spec: dict[str, Any]
    approved_by: str
    approved_at: datetime


class MappingOut(BaseModel):
    id: int
    file_type: FileType
    partner_id: int | None
    channel_id: int | None
    source_name: str
    active_version: int | None
    version_count: int
    updated_at: datetime
