import csv
import gzip
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.api.ingestion_schemas import (
    FileDetail,
    FileOut,
    FilePage,
    ImportDetail,
    ImportRequest,
    JobOut,
    MappingOut,
    MappingVersionOut,
    RowIssueOut,
    RowIssuePage,
    ValidationRequest,
)
from app.core.config import get_settings
from app.core.database import get_db, get_direct_engine
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models import (
    BackgroundJob,
    ColumnMapping,
    ImportJob,
    ImportRowError,
    MappingVersion,
    Partner,
    SalesChannel,
    UploadedFile,
    UploadedFileBlob,
    User,
)
from app.models.enums import FileType, IssueSeverity
from app.services.ingestion import service
from app.services.ingestion.canonical import DATE_FORMATS, SCHEMAS
from app.services.ingestion.spec import header_fingerprint
from app.services.ingestion.suggest import build_draft

router = APIRouter(tags=["ingestion"])

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    """Stops a spreadsheet treating a partner's data as a formula when this file is opened."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def _not_found_file(db: Session, file_id: int) -> UploadedFile:
    file = db.get(UploadedFile, file_id)
    if file is None:
        raise AppError(404, "file_not_found", "That file doesn't exist.")
    return file


@router.get("/ingestion/schema")
def ingestion_schema(_: User = Depends(require(Permission.VIEW_WORKSPACE))) -> dict:
    """The canonical fields, so that the mapping screen never hardcodes them."""
    return {
        "date_formats": list(DATE_FORMATS),
        "file_types": [
            {
                "file_type": schema.file_type.value,
                "label": schema.label,
                "uses_channel": schema.uses_channel,
                "allowed_partner_types": [t.value for t in schema.allowed_partner_types],
                "any_of": [list(group) for group in schema.any_of],
                "fields": [
                    {
                        "name": field.name,
                        "label": field.label,
                        "kind": field.kind,
                        "required": field.required,
                        "enum_values": {
                            value: list(synonyms) for value, synonyms in field.enum_values.items()
                        },
                    }
                    for field in schema.fields
                ],
            }
            for schema in SCHEMAS.values()
        ],
    }


@router.get("/sales-channels")
def list_channels(
    _: User = Depends(require(Permission.VIEW_CONFIG)), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.execute(
        select(SalesChannel.id, SalesChannel.code, SalesChannel.name).order_by(SalesChannel.name)
    )
    return [dict(row._mapping) for row in rows]


@router.post("/files", status_code=201)
async def upload_file(
    request: Request, user: User = Depends(require(Permission.OPERATE_RECONCILIATION))
) -> FileOut:
    settings = get_settings()
    too_large = AppError(413, "file_too_large", f"The limit is {settings.max_upload_mb} MB.")
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        raise AppError(411, "length_required", "The upload must declare its size.")
    # Allows for the multipart wrapper. The file's exact size is checked below.
    if int(declared) > settings.max_upload_bytes + 64 * 1024:
        raise too_large

    form = await request.form(max_files=1, max_fields=8)
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        raise AppError(422, "file_missing", "Choose a file to upload.")
    content = await upload.read()
    if len(content) > settings.max_upload_bytes:
        raise too_large

    def as_int(name: str) -> int | None:
        value = form.get(name)
        if value in (None, ""):
            return None
        if not str(value).isdigit():
            raise AppError(422, "invalid_source", f"{name} must be a number.")
        return int(str(value))

    try:
        file_type = FileType(str(form.get("file_type")))
    except ValueError as exc:
        raise AppError(422, "invalid_file_type", "Choose what kind of file this is.") from exc
    filename = service.safe_filename(upload.filename)
    partner_id, channel_id = as_int("partner_id"), as_int("channel_id")
    client_sha = form.get("sha256")

    def store() -> FileOut:
        # The direct connection, as a pooler isn't the place for a large parameter.
        with Session(get_direct_engine(), expire_on_commit=False) as db:
            file = service.store_upload(
                db,
                filename=filename,
                content=content,
                file_type=file_type,
                partner_id=partner_id,
                channel_id=channel_id,
                client_sha256=str(client_sha) if client_sha else None,
                user=user,
                is_synthetic=settings.demo_mode,
            )
            return service.get_file_out(db, file.id)[0]

    return await run_in_threadpool(store)


@router.get("/files")
def list_files(
    file_type: FileType | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> FilePage:
    items, total = service.list_files(db, file_type=file_type, limit=limit, offset=offset)
    return FilePage(items=items, total=total, limit=limit, offset=offset)


@router.get("/files/{file_id}")
def get_file(
    file_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> FileDetail:
    out, file = service.get_file_out(db, file_id)
    return FileDetail(**out.model_dump(), detection=file.detection)


@router.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> Response:
    file = _not_found_file(db, file_id)
    blob = db.get(UploadedFileBlob, file_id)
    if blob is None:
        raise AppError(404, "file_not_found", "That file's contents are gone.")
    return Response(
        gzip.decompress(blob.content_gzip),
        media_type="application/octet-stream",
        # Always a download, and never rendered, because the content is untrusted.
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}"},
    )


@router.delete("/files/{file_id}", status_code=204)
def delete_file(
    file_id: int,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> None:
    service.delete_file(db, file_id, user)


@router.post("/files/{file_id}/mapping-suggestions")
def mapping_suggestions(
    file_id: int,
    _: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> dict:
    file = _not_found_file(db, file_id)
    detection = service.require_analysed(file)
    file_type = FileType(file.file_type)
    saved = service.saved_mapping(db, file)
    saved_spec = saved.spec if saved else None
    saved_version = saved.version if saved else None
    matches = saved is not None and saved.header_fingerprint == header_fingerprint(
        detection["columns"]
    )
    # Ends the read transaction, so a provider call can't hold a pooled connection open.
    db.rollback()
    return build_draft(file_type, detection, saved_spec, saved_version, matches)


@router.post("/files/{file_id}/validations", status_code=202)
def validate_file(
    file_id: int,
    body: ValidationRequest,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> JobOut:
    file = _not_found_file(db, file_id)
    return JobOut.model_validate(service.start_validation(db, file, body.spec, user))


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> JobOut:
    job = db.get(BackgroundJob, job_id)
    if job is None:
        raise AppError(404, "job_not_found", "That job doesn't exist.")
    return JobOut.model_validate(job)


@router.post("/files/{file_id}/imports", status_code=202)
def start_import(
    file_id: int,
    body: ImportRequest,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> dict:
    file = _not_found_file(db, file_id)
    import_job, created = service.start_import(
        db,
        file,
        body.spec,
        body.validation_job_id,
        body.acknowledged_invalid_rows,
        body.suggested_by,
        body.suggestion,
        user,
    )
    return {"import_id": import_job.id, "created": created}


def _import_detail(db: Session, import_job: ImportJob) -> ImportDetail:
    out, _ = service.get_file_out(db, import_job.file_id)
    version = db.get(MappingVersion, import_job.mapping_version_id)
    return ImportDetail(
        **out.import_job.model_dump(),
        file=out,
        mapping_version=version.version,
        summary=import_job.summary,
        timings_ms=import_job.timings_ms,
    )


def _not_found_import(db: Session, import_id: int) -> ImportJob:
    import_job = db.get(ImportJob, import_id)
    if import_job is None:
        raise AppError(404, "import_not_found", "That import doesn't exist.")
    return import_job


@router.get("/imports/{import_id}")
def get_import(
    import_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> ImportDetail:
    return _import_detail(db, _not_found_import(db, import_id))


@router.post("/imports/{import_id}/retry", status_code=202)
def retry_import(
    import_id: int,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> ImportDetail:
    return _import_detail(db, service.retry_import(db, import_id, user))


def _issue_filters(import_id: int, severity: IssueSeverity | None) -> list:
    filters = [ImportRowError.import_job_id == import_id]
    if severity is not None:
        filters.append(ImportRowError.severity == severity)
    return filters


@router.get("/imports/{import_id}/errors")
def list_import_errors(
    import_id: int,
    severity: IssueSeverity | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> RowIssuePage:
    _not_found_import(db, import_id)
    filters = _issue_filters(import_id, severity)
    total = db.scalar(select(func.count()).select_from(ImportRowError).where(*filters)) or 0
    rank = case(
        (ImportRowError.severity == IssueSeverity.ERROR, 0),
        (ImportRowError.severity == IssueSeverity.DUPLICATE, 1),
        else_=2,
    )
    rows = db.scalars(
        select(ImportRowError)
        .where(*filters)
        .order_by(rank, ImportRowError.row_number, ImportRowError.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return RowIssuePage(
        items=[RowIssueOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/imports/{import_id}/errors.csv")
def download_import_errors(
    import_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> Response:
    _not_found_import(db, import_id)
    rows = db.scalars(
        select(ImportRowError)
        .where(ImportRowError.import_job_id == import_id)
        .order_by(ImportRowError.row_number, ImportRowError.id)
    ).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row_number", "severity", "field", "code", "message", "value_in_file"])
    for row in rows:
        writer.writerow(
            [
                row.row_number,
                row.severity.value,
                row.field or "",
                row.code,
                _csv_safe(row.message),
                _csv_safe(row.raw_value),
            ]
        )
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="import-{import_id}-row-issues.csv"'
        },
    )


@router.get("/mappings")
def list_mappings(
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> list[MappingOut]:
    version_count = (
        select(func.count())
        .where(MappingVersion.mapping_id == ColumnMapping.id)
        .correlate(ColumnMapping)
        .scalar_subquery()
    )
    rows = db.execute(
        select(
            ColumnMapping,
            func.coalesce(Partner.name, SalesChannel.name).label("source_name"),
            MappingVersion.version,
            version_count.label("version_count"),
        )
        .outerjoin(Partner, ColumnMapping.partner_id == Partner.id)
        .outerjoin(SalesChannel, ColumnMapping.channel_id == SalesChannel.id)
        .outerjoin(MappingVersion, ColumnMapping.active_version_id == MappingVersion.id)
        .order_by(ColumnMapping.file_type, "source_name")
    ).all()
    return [
        MappingOut(
            id=mapping.id,
            file_type=mapping.file_type,
            partner_id=mapping.partner_id,
            channel_id=mapping.channel_id,
            source_name=source_name,
            active_version=active,
            version_count=count,
            updated_at=mapping.updated_at,
        )
        for mapping, source_name, active, count in rows
    ]


@router.get("/mappings/{mapping_id}/versions")
def list_mapping_versions(
    mapping_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> list[MappingVersionOut]:
    mapping = db.get(ColumnMapping, mapping_id)
    if mapping is None:
        raise AppError(404, "mapping_not_found", "That mapping doesn't exist.")
    rows = db.execute(
        select(MappingVersion, User.email)
        .join(User, MappingVersion.approved_by == User.id)
        .where(MappingVersion.mapping_id == mapping_id)
        .order_by(MappingVersion.version.desc())
    ).all()
    return [
        service.version_out(version, mapping.active_version_id, email) for version, email in rows
    ]


@router.post("/mappings/{mapping_id}/versions/{version}/restore", status_code=201)
def restore_mapping_version(
    mapping_id: int,
    version: int,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> MappingVersionOut:
    restored = service.restore_version(db, mapping_id, version, user)
    return service.version_out(restored, restored.id, user.email)
