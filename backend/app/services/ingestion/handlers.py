"""What each background job does. This module loads pandas, so only jobs import it."""

import gzip
import time
from collections.abc import Callable

import pandas as pd
from sqlalchemy.orm import Session

from app.core.database import get_direct_engine
from app.models import BackgroundJob, ImportJob, UploadedFile, UploadedFileBlob
from app.models.enums import FileType
from app.services.audit import record_audit
from app.services.ingestion import parsing, pipeline
from app.services.ingestion.spec import MappingSpec
from app.services.jobs import PermanentJobError, complete_job

# Runs just before an import commits. Tests use it to crash the process at the worst moment.
before_import_commit: Callable[[], None] = lambda: None  # noqa: E731


def _session() -> Session:
    return Session(get_direct_engine(), expire_on_commit=False)


def _load_file(db: Session, file_id: int) -> tuple[UploadedFile, bytes]:
    file = db.get(UploadedFile, file_id)
    blob = db.get(UploadedFileBlob, file_id)
    if file is None or blob is None:
        raise PermanentJobError("This file has been deleted.")
    return file, gzip.decompress(blob.content_gzip)


def _read_for_spec(content: bytes, file: UploadedFile, spec: MappingSpec) -> parsing.Table:
    columns = [mapping.column for mapping in spec.fields.values()]
    try:
        return parsing.read_table(
            content, file.filename, sheet=spec.sheet, header_row=spec.header_row, usecols=columns
        )
    except parsing.FileProblem as exc:
        raise PermanentJobError(str(exc)) from exc


def _validate(db: Session, file: UploadedFile, table: parsing.Table, spec: MappingSpec):
    try:
        return pipeline.validate_rows(
            table, spec, FileType(file.file_type), pipeline.courier_lookup(db)
        )
    except parsing.FileProblem as exc:
        raise PermanentJobError(str(exc)) from exc


def run_analysis(job_id: int, worker_id: str) -> None:
    with _session() as db:
        job = db.get(BackgroundJob, job_id)
        file, content = _load_file(db, job.payload["file_id"])
        try:
            detection = parsing.analyse(content, file.filename)
        except parsing.FileProblem as exc:
            raise PermanentJobError(str(exc)) from exc
        file.detection = detection
        complete_job(db, job_id, worker_id, {"row_count": detection["row_count"]})
        db.commit()


def run_validation(job_id: int, worker_id: str) -> None:
    with _session() as db:
        job = db.get(BackgroundJob, job_id)
        file, content = _load_file(db, job.payload["file_id"])
        spec = MappingSpec.model_validate(job.payload["spec"])
        table = _read_for_spec(content, file, spec)
        validated = _validate(db, file, table, spec)
        source = pipeline.source_of(db, file.partner_id, file.channel_id)
        file_type = FileType(file.file_type)
        existing = pipeline.count_existing(db, validated.stage, file_type, source)
        result = {
            "rows_total": validated.rows_total,
            "rows_valid": validated.rows_valid,
            "rows_invalid": validated.rows_invalid,
            "rows_duplicate": validated.rows_duplicate,
            "rows_excluded": validated.rows_excluded,
            "rows_warned": validated.rows_warned,
            # Orders that are already known update in place. Other repeats are skipped.
            "rows_existing": existing,
            "existing_behaviour": "update" if file_type == FileType.ORDER else "skip",
            "issue_groups": pipeline.issue_groups(validated.issues),
            "preview": validated.preview,
        }
        complete_job(db, job_id, worker_id, result)
        db.commit()


def run_import(job_id: int, worker_id: str) -> None:
    timings: dict[str, int] = {}

    def timed(name: str, started: float) -> float:
        now = time.perf_counter()
        timings[name] = int((now - started) * 1000)
        return now

    with _session() as db:
        job = db.get(BackgroundJob, job_id)
        import_job = db.get(ImportJob, job.payload["import_job_id"])
        started = time.perf_counter()
        file, content = _load_file(db, import_job.file_id)
        spec = MappingSpec.model_validate(job.payload["spec"])
        started = timed("read_blob", started)
        table = _read_for_spec(content, file, spec)
        del content
        started = timed("parse", started)
        validated = _validate(db, file, table, spec)
        del table
        started = timed("validate", started)

        file_type = FileType(file.file_type)
        source = pipeline.source_of(db, file.partner_id, file.channel_id)
        loaded = pipeline.load_rows(db, validated.stage, file_type, spec, file.id, source)
        started = timed("load", started)

        issues = validated.issues
        if not loaded.duplicates.empty:
            already = pd.DataFrame(
                {
                    "row_number": loaded.duplicates["row_number"].astype(int).to_numpy(),
                    "field": None,
                    "code": "duplicate_existing",
                    "severity": pipeline.DUPLICATE,
                    "message": (
                        "This order is already recorded with the same details."
                        if file_type == FileType.ORDER
                        else "This record was already imported from an earlier file."
                    ),
                    "raw_value": None,
                }
            )
            issues = pd.concat([issues, already], ignore_index=True)
        truncated = pipeline.store_issues(db, import_job.id, issues)
        started = timed("store_issues", started)

        duplicates = validated.rows_duplicate + len(loaded.duplicates)
        import_job.rows_total = validated.rows_total
        import_job.rows_inserted = loaded.inserted
        import_job.rows_updated = loaded.updated
        import_job.rows_duplicate = duplicates
        import_job.rows_rejected = validated.rows_invalid
        import_job.rows_excluded = validated.rows_excluded
        import_job.rows_warned = validated.rows_warned
        import_job.summary = {
            "issue_groups": pipeline.issue_groups(issues),
            "issues_total": int(len(issues)),
            "issues_truncated": truncated,
        }
        import_job.timings_ms = timings
        record_audit(
            db,
            actor_id=job.created_by,
            action="import.complete",
            entity_type="file",
            entity_id=file.id,
            after={
                "import_id": import_job.id,
                "file_type": file_type.value,
                "inserted": loaded.inserted,
                "updated": loaded.updated,
                "duplicates": duplicates,
                "rejected": validated.rows_invalid,
                "excluded": validated.rows_excluded,
            },
        )
        complete_job(db, job_id, worker_id, {"import_id": import_job.id})
        before_import_commit()
        db.commit()
