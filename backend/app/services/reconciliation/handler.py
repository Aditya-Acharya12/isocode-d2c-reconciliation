"""The reconciliation job. It doesn't need pandas, so it stays light."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_direct_engine
from app.models import BackgroundJob, ReconciliationRun
from app.models.enums import JobStatus
from app.services.jobs import PermanentJobError, complete_job
from app.services.reconciliation.runner import reconcile


def run_reconciliation(job_id: int, worker_id: str) -> None:
    with Session(get_direct_engine(), expire_on_commit=False) as db:
        job = db.get(BackgroundJob, job_id)
        run = db.scalar(
            select(ReconciliationRun).where(ReconciliationRun.background_job_id == job_id)
        )
        if run is None:
            raise PermanentJobError("This reconciliation run no longer exists.")
        run.status = JobStatus.RUNNING
        counts = reconcile(db, run, job.created_by)
        complete_job(db, job_id, worker_id, {"run_id": run.id, **counts})
        db.commit()
