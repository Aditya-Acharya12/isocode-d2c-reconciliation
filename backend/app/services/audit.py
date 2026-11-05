from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Adds an audit row to the caller's transaction, so it commits with the change itself."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            before=before,
            after=after,
        )
    )
