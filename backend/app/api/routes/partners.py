from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require
from app.api.schemas import PartnerCreate, PartnerOut, PartnerUpdate
from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models import Partner, User
from app.services.audit import record_audit

router = APIRouter(prefix="/partners", tags=["partners"])


def _snapshot(partner: Partner) -> dict:
    return {
        "name": partner.name,
        "type": partner.type.value,
        "settlement_terms_days": partner.settlement_terms_days,
        "return_grace_days": partner.return_grace_days,
        "is_active": partner.is_active,
    }


@router.get("")
def list_partners(
    _: User = Depends(require(Permission.VIEW_CONFIG)), db: Session = Depends(get_db)
) -> list[PartnerOut]:
    partners = db.scalars(select(Partner).order_by(Partner.type, Partner.name)).all()
    return [PartnerOut.model_validate(p) for p in partners]


@router.post("", status_code=201)
def create_partner(
    body: PartnerCreate,
    user: User = Depends(require(Permission.MANAGE_CONFIG)),
    db: Session = Depends(get_db),
) -> PartnerOut:
    partner = Partner(**body.model_dump())
    db.add(partner)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "partner_exists", f"A partner named {body.name} exists.") from exc
    record_audit(
        db,
        actor_id=user.id,
        action="partner.create",
        entity_type="partner",
        entity_id=partner.id,
        after=_snapshot(partner),
    )
    db.commit()
    db.refresh(partner)
    return PartnerOut.model_validate(partner)


@router.patch("/{partner_id}")
def update_partner(
    partner_id: int,
    body: PartnerUpdate,
    user: User = Depends(require(Permission.MANAGE_CONFIG)),
    db: Session = Depends(get_db),
) -> PartnerOut:
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise AppError(404, "partner_not_found", "That partner doesn't exist.")
    before = _snapshot(partner)
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(partner, field, value)
    after = _snapshot(partner)
    if after != before:
        record_audit(
            db,
            actor_id=user.id,
            action="partner.update",
            entity_type="partner",
            entity_id=partner.id,
            before=before,
            after=after,
        )
        db.commit()
        db.refresh(partner)
    return PartnerOut.model_validate(partner)
