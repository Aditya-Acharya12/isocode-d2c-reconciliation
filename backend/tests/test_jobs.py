"""The job guarantees: persisted, recoverable, idempotent, and run only once."""

import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import get_direct_engine
from app.models import BackgroundJob, ImportJob, ImportRowError, Order
from app.models.enums import JobKind, JobStatus
from app.services import jobs
from app.services.ingestion import handlers
from app.services.jobs import recover_stale_jobs, run_next, run_until_idle
from tests.conftest import BACKEND_DIR
from tests.ingestion_helpers import (
    draft_for,
    spec_from_draft,
    start_import,
    uploaded,
    validate,
)

ORDERS = (
    b"Order No,Order Date,Order Value,Payment Mode,Status\n"
    b"O1,01/10/2026,100.00,COD,Delivered\n"
    b"O2,01/10/2026,bad,COD,Delivered\n"
    b"O3,01/10/2026,300.00,Prepaid,Delivered\n"
)


def _queued_import(client, sources) -> int:
    file = uploaded(client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(client, file["id"]))
    job = validate(client, file["id"], spec)
    response = start_import(client, file["id"], spec, job)
    assert response.status_code == 202, response.text
    return response.json()["import_id"]


def _background_job(db: Session, import_id: int) -> BackgroundJob:
    db.expire_all()
    return db.get(BackgroundJob, db.get(ImportJob, import_id).background_job_id)


def _order_count(db: Session) -> int:
    db.rollback()
    return db.scalar(select(func.count()).select_from(Order))


def _make_stale(db: Session, job_id: int) -> None:
    db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id)
        .values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=10))
    )
    db.commit()


def test_a_killed_process_leaves_nothing_behind_and_the_job_recovers(
    finance_client, db, sources
):
    """Really kills a process just before commit, with all of its rows already written."""
    import_id = _queued_import(finance_client, sources)
    environment = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    process = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, str(BACKEND_DIR / "tests" / "crash_during_import.py")],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 9, process.stdout + process.stderr

    job = _background_job(db, import_id)
    assert job.status == JobStatus.RUNNING  # it looks as if it's still working
    assert job.attempts == 1
    assert _order_count(db) == 0  # but nothing was committed
    assert db.scalar(select(func.count()).select_from(ImportRowError)) == 0

    # Not stale yet: another instance could really be working on it.
    assert recover_stale_jobs() == {"requeued": 0, "failed": 0}
    assert run_until_idle() == 0

    _make_stale(db, job.id)
    assert recover_stale_jobs() == {"requeued": 1, "failed": 0}
    job = _background_job(db, import_id)
    assert job.status == JobStatus.QUEUED
    assert "restarted" in job.error

    assert run_until_idle() == 1
    job = _background_job(db, import_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert job.error is None
    assert _order_count(db) == 2  # exactly once
    result = finance_client.get(f"/api/v1/imports/{import_id}").json()
    assert result["rows_inserted"] == 2
    assert result["rows_rejected"] == 1
    # Row issues aren't duplicated by the second attempt either.
    assert db.scalar(select(func.count()).select_from(ImportRowError)) == 1


def test_jobs_interrupted_too_often_fail_visibly(finance_client, db, sources):
    import_id = _queued_import(finance_client, sources)
    job = _background_job(db, import_id)
    db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(status=JobStatus.RUNNING, attempts=job.max_attempts, locked_by="gone")
    )
    db.commit()
    _make_stale(db, job.id)
    assert recover_stale_jobs() == {"requeued": 0, "failed": 1}
    job = _background_job(db, import_id)
    assert job.status == JobStatus.FAILED
    assert "stopped after" in job.error
    detail = finance_client.get(f"/api/v1/imports/{import_id}").json()
    assert detail["file"]["stage"] == "import_failed"
    assert "stopped after" in detail["file"]["error"]


def test_unexpected_failures_retry_then_fail_then_can_be_retried_by_a_person(
    finance_client, db, sources
):
    import_id = _queued_import(finance_client, sources)
    with patch.object(handlers.pipeline, "load_rows", side_effect=RuntimeError("private detail")):
        assert run_until_idle() == 3  # the first attempt, and two automatic retries
    job = _background_job(db, import_id)
    assert job.status == JobStatus.FAILED
    assert job.attempts == 3
    assert "RuntimeError" in job.error
    assert "private detail" not in job.error  # internals don't reach users
    assert _order_count(db) == 0

    response = finance_client.post(f"/api/v1/imports/{import_id}/retry")
    assert response.status_code == 202
    assert run_until_idle() == 1
    job = _background_job(db, import_id)
    assert job.status == JobStatus.SUCCEEDED
    assert _order_count(db) == 2


def test_only_failed_imports_can_be_retried(finance_client, sources):
    import_id = _queued_import(finance_client, sources)
    assert finance_client.post(f"/api/v1/imports/{import_id}/retry").status_code == 409
    run_until_idle()
    assert finance_client.post(f"/api/v1/imports/{import_id}/retry").status_code == 409


def test_a_failure_partway_through_rolls_everything_back(finance_client, db, sources):
    import_id = _queued_import(finance_client, sources)
    with patch.object(handlers.pipeline, "store_issues", side_effect=RuntimeError("boom")):
        run_next("worker-1")  # the orders were written, and then this failed
    assert _order_count(db) == 0
    assert _background_job(db, import_id).status == JobStatus.QUEUED


def test_problems_with_the_file_itself_are_not_retried(finance_client, db, sources):
    file = uploaded(finance_client, b"a,b\n", "o.csv", "order", channel_id=sources["channel"])
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.kind == JobKind.FILE_ANALYSIS))
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert file["stage"] == "analysis_failed"


def test_a_worker_that_lost_its_claim_commits_nothing(finance_client, db, sources):
    """If a slow worker is presumed dead and replaced, the original must not also write."""
    import_id = _queued_import(finance_client, sources)
    job_id = _background_job(db, import_id).id
    original = handlers.before_import_commit

    def takeover() -> None:
        raise AssertionError("complete_job should have refused first")

    real_load = handlers.pipeline.load_rows

    def load_then_lose_the_claim(*args, **kwargs):
        result = real_load(*args, **kwargs)
        with Session(get_direct_engine()) as other:
            other.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .values(locked_by="a-different-worker")
            )
            other.commit()
        return result

    handlers.before_import_commit = takeover
    try:
        with patch.object(handlers.pipeline, "load_rows", load_then_lose_the_claim):
            assert run_next("worker-slow") is True
    finally:
        handlers.before_import_commit = original
    assert _order_count(db) == 0
    assert _background_job(db, import_id).locked_by == "a-different-worker"


def test_two_workers_never_claim_the_same_job(finance_client, db, sources):
    _queued_import(finance_client, sources)
    claims: list = []
    barrier = threading.Barrier(4)

    def claim(index: int) -> None:
        barrier.wait()
        claims.append(jobs._claim_next(f"worker-{index}"))

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([claim for claim in claims if claim is not None]) == 1


def test_the_idle_worker_never_polls_the_database(db, users):
    """A timer would stop Neon scaling to zero, and use up its monthly compute."""
    worker = jobs.JobWorker()
    assert worker._cycle() is None  # None means "sleep until woken"


def test_a_job_running_elsewhere_is_rechecked_on_a_timer(finance_client, db, sources):
    import_id = _queued_import(finance_client, sources)
    job = _background_job(db, import_id)
    db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(
            status=JobStatus.RUNNING,
            locked_by="overlapping-instance",
            heartbeat_at=datetime.now(UTC),
        )
    )
    db.commit()
    assert jobs.JobWorker()._cycle() == jobs.RECHECK_SECONDS


def test_the_worker_thread_runs_jobs_when_notified(finance_client, db, sources):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    response = finance_client.post(f"/api/v1/files/{file['id']}/validations", json={"spec": spec})
    job_id = response.json()["id"]

    worker = jobs.JobWorker()
    worker.start()
    try:
        worker.notify()
        for _ in range(100):
            db.expire_all()
            job = db.get(BackgroundJob, job_id)
            if job.status == JobStatus.SUCCEEDED:
                break
            threading.Event().wait(0.1)
        assert job.status == JobStatus.SUCCEEDED
    finally:
        worker.stop()


def test_recovery_on_startup_picks_up_interrupted_work(finance_client, db, sources):
    import_id = _queued_import(finance_client, sources)
    job = _background_job(db, import_id)
    db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(status=JobStatus.RUNNING, attempts=1, locked_by="dead-instance")
    )
    db.commit()
    _make_stale(db, job.id)

    worker = jobs.JobWorker()
    assert worker._cycle() is None  # one cycle both recovers it and runs it
    assert _background_job(db, import_id).status == JobStatus.SUCCEEDED
    assert _order_count(db) == 2


@pytest.mark.parametrize("kind", [JobKind.FILE_ANALYSIS, JobKind.VALIDATION, JobKind.IMPORT])
def test_deleted_files_fail_their_jobs_cleanly(db, users, kind):
    job = BackgroundJob(
        kind=kind,
        payload={"file_id": 999999, "import_job_id": 999999, "spec": {"fields": {}}},
        idempotency_key=f"test:{kind}",
        created_by=users["finance"].id,
    )
    db.add(job)
    db.commit()
    with patch.object(jobs, "logger"):
        run_until_idle()
    db.expire_all()
    assert db.get(BackgroundJob, job.id).status == JobStatus.FAILED
