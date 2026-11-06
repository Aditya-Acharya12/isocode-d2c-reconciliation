"""Database operations behind the ingestion API. Doesn't load pandas."""

import gzip
import hashlib
import re
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.api.ingestion_schemas import FileOut, ImportCounts, MappingVersionOut
from app.core.errors import AppError
from app.models import (
    BackgroundJob,
    ColumnMapping,
    ImportJob,
    MappingVersion,
    Partner,
    SalesChannel,
    UploadedFile,
    UploadedFileBlob,
    User,
)
from app.models.enums import FileType, JobKind, JobStatus, MappingSource
from app.services.audit import record_audit
from app.services.ingestion.canonical import schema_for
from app.services.ingestion.spec import (
    MappingSpec,
    header_fingerprint,
    spec_hash,
    spec_problems,
)
from app.services.jobs import worker

ALLOWED_SUFFIXES = {"csv", "xlsx"}
ANALYSIS_KEY = "analyse:{}"
IMPORT_KEY = "import:{}"


def safe_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[-255:]


def check_file(filename: str, size: int, content: bytes) -> None:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "xls":
        raise AppError(
            422, "unsupported_format", "Old .xls files aren't supported. Save it as .xlsx."
        )
    if suffix not in ALLOWED_SUFFIXES:
        raise AppError(422, "unsupported_format", "Only .csv and .xlsx files are supported.")
    if size == 0:
        raise AppError(422, "empty_file", "This file is empty.")
    is_zip = content.startswith(b"PK")
    if suffix == "xlsx" and not is_zip:
        raise AppError(422, "unsupported_format", "This doesn't look like an Excel .xlsx file.")
    if suffix == "csv" and (is_zip or content.startswith(b"%PDF")):
        raise AppError(422, "unsupported_format", "This doesn't look like a CSV file.")


def check_source(
    db: Session, file_type: FileType, partner_id: int | None, channel_id: int | None
) -> None:
    schema = schema_for(file_type)
    if schema.uses_channel:
        if channel_id is None or partner_id is not None:
            raise AppError(422, "invalid_source", "Choose the sales channel for this order file.")
        if db.get(SalesChannel, channel_id) is None:
            raise AppError(422, "invalid_source", "That sales channel doesn't exist.")
        return
    if partner_id is None or channel_id is not None:
        raise AppError(422, "invalid_source", f"Choose the partner for this {schema.label} file.")
    partner = db.get(Partner, partner_id)
    if partner is None or not partner.is_active:
        raise AppError(422, "invalid_source", "That partner doesn't exist or isn't active.")
    if partner.type not in schema.allowed_partner_types:
        allowed = ", ".join(t.value.replace("_", " ") for t in schema.allowed_partner_types)
        raise AppError(
            422,
            "invalid_source",
            f"{schema.label} files come from a {allowed} partner, and {partner.name} isn't one.",
        )


def enqueue(
    db: Session, kind: JobKind, payload: dict, key: str, user_id: int
) -> tuple[BackgroundJob, bool]:
    """Returns the job for this key, and whether it was newly created. Doesn't commit."""
    existing = db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))
    if existing is not None:
        return existing, False
    job = BackgroundJob(kind=kind, payload=payload, idempotency_key=key, created_by=user_id)
    try:
        with db.begin_nested():
            db.add(job)
    except IntegrityError:
        # Another request queued the same work at this moment. That's the one to use.
        return db.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key)), False
    return job, True


def store_upload(
    db: Session,
    *,
    filename: str,
    content: bytes,
    file_type: FileType,
    partner_id: int | None,
    channel_id: int | None,
    client_sha256: str | None,
    user: User,
    is_synthetic: bool,
) -> UploadedFile:
    check_file(filename, len(content), content)
    check_source(db, file_type, partner_id, channel_id)
    digest = hashlib.sha256(content).hexdigest()
    if client_sha256 and client_sha256.lower() != digest:
        raise AppError(
            422,
            "upload_corrupted",
            "The file changed on its way to the server, so it wasn't saved. Please try again.",
        )

    # The same content under any source is refused: a mistaken re-upload against a
    # different partner would otherwise count the same money twice.
    existing = db.scalar(
        select(UploadedFile)
        .where(UploadedFile.sha256 == digest, UploadedFile.file_type == file_type)
        .limit(1)
    )
    if existing is not None:
        raise AppError(
            409,
            "duplicate_file",
            f"This file was already uploaded as '{existing.filename}'. "
            "Open that upload, or delete it first if it was for the wrong partner.",
            {"file_id": existing.id},
        )

    file = UploadedFile(
        partner_id=partner_id,
        channel_id=channel_id,
        file_type=file_type,
        filename=filename,
        sha256=digest,
        size_bytes=len(content),
        uploaded_by=user.id,
        is_synthetic=is_synthetic,
    )
    db.add(file)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "duplicate_file", "This file was already uploaded.") from exc
    # Level 1 keeps this fast on a small instance. Text still compresses very well.
    db.add(UploadedFileBlob(file_id=file.id, content_gzip=gzip.compress(content, compresslevel=1)))
    enqueue(db, JobKind.FILE_ANALYSIS, {"file_id": file.id}, ANALYSIS_KEY.format(file.id), user.id)
    record_audit(
        db,
        actor_id=user.id,
        action="file.upload",
        entity_type="file",
        entity_id=file.id,
        after={
            "filename": filename,
            "file_type": file_type.value,
            "partner_id": partner_id,
            "channel_id": channel_id,
            "sha256": digest,
            "size_bytes": len(content),
        },
    )
    db.commit()
    worker.notify()
    return file


# --- Reading ---------------------------------------------------------------------------


def _stage(
    analysis: BackgroundJob | None, background: BackgroundJob | None
) -> tuple[str, str | None]:
    if background is not None:
        if background.status == JobStatus.SUCCEEDED:
            return "imported", None
        if background.status == JobStatus.FAILED:
            return "import_failed", background.error
        return "importing", None
    if analysis is None or analysis.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return "analysing", None
    if analysis.status == JobStatus.FAILED:
        return "analysis_failed", analysis.error
    return "needs_mapping", None


def _files_query():
    analysis = aliased(BackgroundJob)
    background = aliased(BackgroundJob)
    return (
        select(
            UploadedFile,
            func.coalesce(Partner.name, SalesChannel.name).label("source_name"),
            User.email,
            analysis,
            ImportJob,
            background,
        )
        .join(User, UploadedFile.uploaded_by == User.id)
        .outerjoin(Partner, UploadedFile.partner_id == Partner.id)
        .outerjoin(SalesChannel, UploadedFile.channel_id == SalesChannel.id)
        .outerjoin(
            analysis,
            analysis.idempotency_key == func.concat("analyse:", UploadedFile.id),
        )
        .outerjoin(ImportJob, ImportJob.file_id == UploadedFile.id)
        .outerjoin(background, ImportJob.background_job_id == background.id)
    )


def _import_counts(import_job: ImportJob | None, background: BackgroundJob | None):
    if import_job is None or background is None:
        return None
    return ImportCounts(
        id=import_job.id,
        status=background.status,
        rows_total=import_job.rows_total,
        rows_inserted=import_job.rows_inserted,
        rows_updated=import_job.rows_updated,
        rows_duplicate=import_job.rows_duplicate,
        rows_rejected=import_job.rows_rejected,
        rows_excluded=import_job.rows_excluded,
        rows_warned=import_job.rows_warned,
        error=background.error,
        attempts=background.attempts,
        started_at=background.started_at,
        finished_at=background.finished_at,
    )


def _file_out(row) -> FileOut:
    file, source_name, email, analysis, import_job, background = row
    stage, error = _stage(analysis, background)
    return FileOut(
        id=file.id,
        filename=file.filename,
        file_type=file.file_type,
        partner_id=file.partner_id,
        channel_id=file.channel_id,
        source_name=source_name,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        uploaded_by=email,
        uploaded_at=file.created_at,
        is_synthetic=file.is_synthetic,
        stage=stage,
        error=error,
        import_job=_import_counts(import_job, background),
    )


def list_files(
    db: Session, *, file_type: FileType | None, limit: int, offset: int
) -> tuple[list[FileOut], int]:
    filters = [UploadedFile.file_type == file_type] if file_type else []
    total = db.scalar(select(func.count()).select_from(UploadedFile).where(*filters)) or 0
    rows = db.execute(
        _files_query().where(*filters).order_by(UploadedFile.id.desc()).limit(limit).offset(offset)
    ).all()
    return [_file_out(row) for row in rows], total


def get_file_out(db: Session, file_id: int) -> tuple[FileOut, UploadedFile]:
    row = db.execute(_files_query().where(UploadedFile.id == file_id)).one_or_none()
    if row is None:
        raise AppError(404, "file_not_found", "That file doesn't exist.")
    return _file_out(row), row[0]


# --- Mappings --------------------------------------------------------------------------


def _mapping_for(db: Session, file: UploadedFile) -> ColumnMapping | None:
    return db.scalar(
        select(ColumnMapping).where(
            ColumnMapping.file_type == file.file_type,
            ColumnMapping.partner_id.is_not_distinct_from(file.partner_id),
            ColumnMapping.channel_id.is_not_distinct_from(file.channel_id),
        )
    )


def saved_mapping(db: Session, file: UploadedFile) -> MappingVersion | None:
    mapping = _mapping_for(db, file)
    if mapping is None or mapping.active_version_id is None:
        return None
    return db.get(MappingVersion, mapping.active_version_id)


_SOURCES = {"gemini": MappingSource.AI, "heuristic": MappingSource.HEURISTIC}


def approve_mapping(
    db: Session,
    file: UploadedFile,
    spec: MappingSpec,
    columns: list[str],
    suggested_by: str,
    suggestion: dict | None,
    user: User,
) -> MappingVersion:
    """Saves a person's approved mapping, as a new version only if it actually changed."""
    fingerprint = header_fingerprint(columns)
    spec_data = spec.model_dump()
    mapping = _mapping_for(db, file)
    if mapping is None:
        mapping = ColumnMapping(
            partner_id=file.partner_id, channel_id=file.channel_id, file_type=file.file_type
        )
        db.add(mapping)
        db.flush()
    current = (
        db.get(MappingVersion, mapping.active_version_id) if mapping.active_version_id else None
    )
    if (
        current is not None
        and current.header_fingerprint == fingerprint
        and spec_hash(MappingSpec.model_validate(current.spec)) == spec_hash(spec)
    ):
        return current
    latest = db.scalar(
        select(func.max(MappingVersion.version)).where(MappingVersion.mapping_id == mapping.id)
    )
    version = MappingVersion(
        mapping_id=mapping.id,
        version=(latest or 0) + 1,
        spec=spec_data,
        header_fingerprint=fingerprint,
        source=_SOURCES.get(suggested_by, MappingSource.MANUAL),
        suggestion=suggestion,
        approved_by=user.id,
        approved_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    mapping.active_version_id = version.id
    record_audit(
        db,
        actor_id=user.id,
        action="mapping.approve",
        entity_type="mapping",
        entity_id=mapping.id,
        before={"version": current.version} if current else None,
        after={
            "version": version.version,
            "suggested_by": suggested_by,
            "fields": sorted(spec.fields),
        },
    )
    return version


def restore_version(
    db: Session, mapping_id: int, version_number: int, user: User
) -> MappingVersion:
    mapping = db.get(ColumnMapping, mapping_id)
    if mapping is None:
        raise AppError(404, "mapping_not_found", "That mapping doesn't exist.")
    target = db.scalar(
        select(MappingVersion).where(
            MappingVersion.mapping_id == mapping_id, MappingVersion.version == version_number
        )
    )
    if target is None:
        raise AppError(404, "version_not_found", "That mapping version doesn't exist.")
    if mapping.active_version_id == target.id:
        raise AppError(409, "already_active", f"Version {version_number} is already active.")
    latest = db.scalar(
        select(func.max(MappingVersion.version)).where(MappingVersion.mapping_id == mapping_id)
    )
    # A copy keeps the history linear: what was active, and when, is never rewritten.
    restored = MappingVersion(
        mapping_id=mapping_id,
        version=latest + 1,
        spec=target.spec,
        header_fingerprint=target.header_fingerprint,
        source=MappingSource.MANUAL,
        suggestion=None,
        approved_by=user.id,
        approved_at=datetime.now(UTC),
    )
    db.add(restored)
    db.flush()
    mapping.active_version_id = restored.id
    record_audit(
        db,
        actor_id=user.id,
        action="mapping.restore",
        entity_type="mapping",
        entity_id=mapping_id,
        after={"restored_from": version_number, "version": restored.version},
    )
    db.commit()
    return restored


def version_out(version: MappingVersion, active_id: int | None, email: str) -> MappingVersionOut:
    return MappingVersionOut(
        id=version.id,
        version=version.version,
        is_active=version.id == active_id,
        source=version.source,
        spec=version.spec,
        approved_by=email,
        approved_at=version.approved_at,
    )


# --- Starting work ---------------------------------------------------------------------


def require_analysed(file: UploadedFile) -> dict:
    if not file.detection:
        raise AppError(409, "not_analysed", "This file hasn't finished being analysed yet.")
    return file.detection


def start_validation(
    db: Session, file: UploadedFile, spec: MappingSpec, user: User
) -> BackgroundJob:
    detection = require_analysed(file)
    problems = spec_problems(spec, FileType(file.file_type), detection["columns"])
    if problems:
        raise AppError(422, "invalid_mapping", problems[0], problems)
    job, created = enqueue(
        db,
        JobKind.VALIDATION,
        {"file_id": file.id, "spec": spec.model_dump()},
        f"validate:{file.id}:{spec_hash(spec)}",
        user.id,
    )
    if not created and job.status == JobStatus.FAILED:
        job.status = JobStatus.QUEUED
        job.attempts = 0
        job.error = None
        job.finished_at = None
    db.commit()
    worker.notify()
    return job


def start_import(
    db: Session,
    file: UploadedFile,
    spec: MappingSpec,
    validation_job_id: int,
    acknowledged_invalid_rows: int,
    suggested_by: str,
    suggestion: dict | None,
    user: User,
) -> tuple[ImportJob, bool]:
    """Approves the mapping and queues the import, together. A repeat does nothing new."""
    existing = db.scalar(select(ImportJob).where(ImportJob.file_id == file.id))
    if existing is not None:
        return existing, False

    detection = require_analysed(file)
    problems = spec_problems(spec, FileType(file.file_type), detection["columns"])
    if problems:
        raise AppError(422, "invalid_mapping", problems[0], problems)

    validation = db.get(BackgroundJob, validation_job_id)
    expected_key = f"validate:{file.id}:{spec_hash(spec)}"
    if validation is None or validation.idempotency_key != expected_key:
        raise AppError(
            409,
            "validation_required",
            "This mapping changed after it was validated. Validate it again before importing.",
        )
    if validation.status != JobStatus.SUCCEEDED or not validation.result:
        raise AppError(409, "validation_required", "Validation hasn't finished yet.")
    result = validation.result
    if result["rows_valid"] == 0:
        raise AppError(422, "nothing_to_import", "No rows in this file can be imported.")
    if acknowledged_invalid_rows != result["rows_invalid"]:
        raise AppError(
            409,
            "acknowledgement_required",
            f"{result['rows_invalid']} rows won't be imported. Please review and confirm them.",
            {"rows_invalid": result["rows_invalid"]},
        )

    version = approve_mapping(
        db, file, spec, detection["columns"], suggested_by, suggestion, user
    )
    job, created = enqueue(
        db,
        JobKind.IMPORT,
        {"file_id": file.id, "spec": spec.model_dump()},
        IMPORT_KEY.format(file.id),
        user.id,
    )
    import_job = ImportJob(file_id=file.id, mapping_version_id=version.id, background_job_id=job.id)
    db.add(import_job)
    try:
        db.flush()
    except IntegrityError:
        # Someone else started it at the same moment, which is exactly what must be safe.
        db.rollback()
        return db.scalar(select(ImportJob).where(ImportJob.file_id == file.id)), False
    job.payload = {**job.payload, "import_job_id": import_job.id}
    record_audit(
        db,
        actor_id=user.id,
        action="import.start",
        entity_type="file",
        entity_id=file.id,
        after={
            "import_id": import_job.id,
            "mapping_version": version.version,
            "rows_valid": result["rows_valid"],
            "rows_invalid": result["rows_invalid"],
        },
    )
    db.commit()
    worker.notify()
    return import_job, True


def retry_import(db: Session, import_id: int, user: User) -> ImportJob:
    import_job = db.get(ImportJob, import_id)
    if import_job is None:
        raise AppError(404, "import_not_found", "That import doesn't exist.")
    job = db.get(BackgroundJob, import_job.background_job_id, with_for_update=True)
    if job.status != JobStatus.FAILED:
        raise AppError(409, "not_failed", "Only a failed import can be retried.")
    job.status = JobStatus.QUEUED
    job.attempts = 0
    job.error = None
    job.finished_at = None
    record_audit(
        db,
        actor_id=user.id,
        action="import.retry",
        entity_type="file",
        entity_id=import_job.file_id,
        after={"import_id": import_id},
    )
    db.commit()
    worker.notify()
    return import_job


def delete_file(db: Session, file_id: int, user: User) -> None:
    file = db.get(UploadedFile, file_id)
    if file is None:
        raise AppError(404, "file_not_found", "That file doesn't exist.")
    if db.scalar(select(ImportJob.id).where(ImportJob.file_id == file_id)) is not None:
        raise AppError(
            409, "file_imported", "This file has been imported, so it can't be deleted."
        )
    running = db.scalar(
        select(BackgroundJob.id).where(
            BackgroundJob.status == JobStatus.RUNNING,
            BackgroundJob.payload["file_id"].as_integer() == file_id,
        )
    )
    if running is not None:
        raise AppError(409, "file_busy", "This file is being processed. Try again in a moment.")
    jobs = db.scalars(
        select(BackgroundJob).where(BackgroundJob.payload["file_id"].as_integer() == file_id)
    ).all()
    for job in jobs:
        db.delete(job)
    record_audit(
        db,
        actor_id=user.id,
        action="file.delete",
        entity_type="file",
        entity_id=file_id,
        before={"filename": file.filename, "file_type": file.file_type.value},
    )
    db.delete(file)
    db.commit()
