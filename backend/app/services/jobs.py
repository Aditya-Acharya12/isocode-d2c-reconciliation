"""The persisted job queue, and the thread that works through it.

Guarantees (see docs/architecture.md section 14):
- Persisted: every job is a row, written before any work starts.
- Recoverable: a running job with a stale heartbeat goes back in the queue.
- Idempotent: a job commits its effects and its status in one transaction.
- Single execution: jobs are claimed with FOR UPDATE SKIP LOCKED.

The worker sleeps until a job arrives, and doesn't poll the database while idle. A
timer would keep Neon awake permanently, and use up its monthly compute allowance.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_direct_engine
from app.models import BackgroundJob
from app.models.enums import JobKind, JobStatus
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15
# Generous, because a small instance can stall for a while under load.
STALE_AFTER_SECONDS = 120
RECHECK_SECONDS = 60
RETRY_AFTER_DATABASE_ERROR_SECONDS = 15
INTERRUPTED_MESSAGE = "The server restarted while this was running."


class PermanentJobError(Exception):
    """Retrying can't help, such as with a damaged file. The message is shown to the user."""


class LostClaim(Exception):
    """Another worker took this job over, so this one must not commit anything."""


def _session() -> Session:
    return Session(get_direct_engine(), expire_on_commit=False)


def recover_stale_jobs(now: datetime | None = None) -> dict[str, int]:
    """Returns interrupted jobs to the queue, or fails them after too many attempts."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=STALE_AFTER_SECONDS)
    requeued = failed = 0
    with _session() as db:
        stale = db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.status == JobStatus.RUNNING,
                BackgroundJob.heartbeat_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.locked_by = None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.finished_at = now
                job.error = f"{INTERRUPTED_MESSAGE} It stopped after {job.attempts} attempts."
                _record_import_outcome(db, job, "import.failed")
                failed += 1
            else:
                job.status = JobStatus.QUEUED
                job.error = f"{INTERRUPTED_MESSAGE} It will run again."
                requeued += 1
        db.commit()
    if requeued or failed:
        logger.warning("Recovered interrupted jobs: %s requeued, %s failed", requeued, failed)
    return {"requeued": requeued, "failed": failed}


def _claim_next(worker_id: str) -> tuple[int, JobKind, int] | None:
    with _session() as db:
        job = db.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.QUEUED)
            .order_by(BackgroundJob.created_at, BackgroundJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        now = datetime.now(UTC)
        job.status = JobStatus.RUNNING
        job.locked_by = worker_id
        job.attempts += 1
        job.started_at = now
        job.heartbeat_at = now
        job.finished_at = None
        db.commit()
        return job.id, job.kind, job.attempts


class _Heartbeat:
    def __init__(self, job_id: int, worker_id: str) -> None:
        self._job_id = job_id
        self._worker_id = worker_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="job-heartbeat")

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with get_direct_engine().begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE background_jobs SET heartbeat_at = now() "
                            "WHERE id = :id AND locked_by = :worker AND status = 'running'"
                        ),
                        {"id": self._job_id, "worker": self._worker_id},
                    )
            except Exception as exc:  # noqa: BLE001 - a missed beat must not stop the job
                logger.warning("Job heartbeat failed: %s", exc.__class__.__name__)

    def __enter__(self) -> "_Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def complete_job(db: Session, job_id: int, worker_id: str, result: dict | None) -> None:
    """Marks success inside the caller's transaction, so it commits with the work itself."""
    job = db.get(BackgroundJob, job_id, with_for_update=True, populate_existing=True)
    if job is None or job.locked_by != worker_id or job.status != JobStatus.RUNNING:
        raise LostClaim
    job.status = JobStatus.SUCCEEDED
    job.result = result
    job.error = None
    job.finished_at = datetime.now(UTC)
    job.locked_by = None


def _record_import_outcome(db: Session, job: BackgroundJob, action: str) -> None:
    if job.kind != JobKind.IMPORT:
        return
    record_audit(
        db,
        actor_id=job.created_by,
        action=action,
        entity_type="file",
        entity_id=job.payload.get("file_id"),
        after={"job_id": job.id, "error": job.error},
    )


def _record_failure(job_id: int, worker_id: str, message: str, retry: bool) -> None:
    with _session() as db:
        job = db.get(BackgroundJob, job_id, with_for_update=True)
        if job is None or job.locked_by != worker_id:
            return
        job.locked_by = None
        if retry and job.attempts < job.max_attempts:
            job.status = JobStatus.QUEUED
            job.error = f"{message} It will run again."
        else:
            job.status = JobStatus.FAILED
            job.error = message
            job.finished_at = datetime.now(UTC)
            _record_import_outcome(db, job, "import.failed")
        db.commit()


def _handlers() -> dict[JobKind, Callable[[int, str], None]]:
    # Imported here, because this is what loads pandas. An idle server never pays for it.
    from app.services.ingestion import handlers

    return {
        JobKind.FILE_ANALYSIS: handlers.run_analysis,
        JobKind.VALIDATION: handlers.run_validation,
        JobKind.IMPORT: handlers.run_import,
    }


def run_next(worker_id: str) -> bool:
    """Runs one job, if there is one. Returns whether one was found."""
    claimed = _claim_next(worker_id)
    if claimed is None:
        return False
    job_id, kind, _ = claimed
    try:
        with _Heartbeat(job_id, worker_id):
            _handlers()[kind](job_id, worker_id)
    except LostClaim:
        logger.warning("Job %s was taken over by another worker", job_id)
    except PermanentJobError as exc:
        _record_failure(job_id, worker_id, str(exc), retry=False)
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not lost
        # Only the type is logged. Database errors can quote the row being written.
        logger.error("Job %s failed: %s", job_id, exc.__class__.__name__)
        _record_failure(
            job_id, worker_id, f"Processing failed unexpectedly ({exc.__class__.__name__}).", True
        )
    return True


def run_until_idle(worker_id: str | None = None) -> int:
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
    count = 0
    while run_next(worker_id):
        count += 1
    return count


def _others_running() -> bool:
    with _session() as db:
        return (
            db.scalar(
                select(BackgroundJob.id).where(BackgroundJob.status == JobStatus.RUNNING).limit(1)
            )
            is not None
        )


class JobWorker:
    """One background thread, working through jobs one at a time."""

    def __init__(self) -> None:
        self._id = f"worker-{uuid.uuid4().hex[:12]}"
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-worker")
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _cycle(self) -> float | None:
        """Does all available work. Returns how long to sleep, where None means until woken."""
        recover_stale_jobs()
        while not self._stopping.is_set() and run_next(self._id):
            pass
        # A job still marked running belongs to a crashed or overlapping instance, and
        # only time can show which. This is the one case that wakes on a timer.
        return RECHECK_SECONDS if _others_running() else None

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                delay = self._cycle()
            except Exception as exc:  # noqa: BLE001 - the worker must outlive database outages
                logger.warning("Job worker cycle failed: %s", exc.__class__.__name__)
                delay = RETRY_AFTER_DATABASE_ERROR_SECONDS
            self._wake.wait(delay)
            self._wake.clear()


worker = JobWorker()
