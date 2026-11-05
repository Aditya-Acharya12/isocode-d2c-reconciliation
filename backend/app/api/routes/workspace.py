from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.api.schemas import WorkspaceSummaryOut
from app.core.database import get_db
from app.core.permissions import Permission
from app.models import Claim, Order, Partner, ReconciliationRun, SalesChannel, UploadedFile, User
from app.models.enums import ClaimStatus

router = APIRouter(prefix="/workspace", tags=["workspace"])

_CLOSED_CLAIM_STATUSES = (ClaimStatus.RECOVERED, ClaimStatus.REJECTED, ClaimStatus.WRITTEN_OFF)


@router.get("/summary")
def workspace_summary(
    _: User = Depends(require(Permission.VIEW_WORKSPACE)), db: Session = Depends(get_db)
) -> WorkspaceSummaryOut:
    def count(model):
        return select(func.count()).select_from(model).scalar_subquery()

    row = db.execute(
        select(
            count(Partner).label("partners"),
            count(SalesChannel).label("sales_channels"),
            count(UploadedFile).label("uploaded_files"),
            count(Order).label("orders"),
            count(ReconciliationRun).label("reconciliation_runs"),
            select(func.count())
            .select_from(Claim)
            .where(Claim.status.not_in(_CLOSED_CLAIM_STATUSES))
            .scalar_subquery()
            .label("open_claims"),
        )
    ).one()
    return WorkspaceSummaryOut(**row._mapping)
