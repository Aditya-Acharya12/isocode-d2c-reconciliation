from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.schemas import AuditLogOut, Page
from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import Permission, has_permission
from app.models import AuditLog, User

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[AuditLogOut]:
    filters = []
    if not has_permission(user.role, Permission.VIEW_ALL_AUDIT):
        if not has_permission(user.role, Permission.VIEW_OWN_AUDIT):
            raise AppError(403, "forbidden", "Your role doesn't allow this action.")
        filters.append(AuditLog.actor_id == user.id)

    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = db.execute(
        select(AuditLog, User.email)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .where(*filters)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        AuditLogOut(
            id=log.id,
            actor_email=email,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            before=log.before,
            after=log.after,
            created_at=log.created_at,
        )
        for log, email in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)
