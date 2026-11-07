from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models import Claim, ClaimEvent, Discrepancy, Order, User
from app.models.enums import ClaimStatus
from app.services.reconciliation import claims as service

router = APIRouter(tags=["claims"])


def _claim_out(claim: Claim, partners: dict[int, str], order_ids: dict[int, str]) -> dict:
    return {
        "id": claim.id,
        "claim_no": claim.claim_no,
        "partner_id": claim.partner_id,
        "partner": partners.get(claim.partner_id),
        "order_id": claim.order_id,
        "external_order_id": order_ids.get(claim.order_id),
        "category": claim.category.value,
        "expected": str(claim.expected),
        "actual": str(claim.actual),
        "difference": str(claim.difference),
        "claimed": str(service.claimed_amount(claim)),
        "recovered": str(claim.recovered_amount),
        "status": claim.status.value,
        "resolution_notes": claim.resolution_notes,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }


def _order_ids(db: Session, ids) -> dict[int, str]:
    return dict(
        db.execute(select(Order.id, Order.external_order_id).where(Order.id.in_(set(ids)))).all()
    )


@router.get("/claims/eligible")
def eligible(
    _: User = Depends(require(Permission.VIEW_WORKSPACE)), db: Session = Depends(get_db)
) -> list[dict]:
    """Open discrepancies that can be claimed, grouped by the partner who owes them."""
    partners = service.partner_names(db)
    discrepancies = service.eligible(db)
    orders = _order_ids(db, [d.order_id for d in discrepancies])
    groups: dict[int, dict] = {}
    for d in discrepancies:
        group = groups.setdefault(
            d.counterparty_partner_id,
            {
                "partner_id": d.counterparty_partner_id,
                "partner": partners.get(d.counterparty_partner_id),
                "total": Decimal("0"),
                "items": [],
            },
        )
        group["total"] += abs(d.difference or 0)
        group["items"].append(
            {
                "discrepancy_id": d.id,
                "order_id": d.order_id,
                "external_order_id": orders.get(d.order_id),
                "category": d.category.value,
                "expected": str(d.expected),
                "actual": str(d.actual),
                "difference": str(d.difference),
                "due_date": d.due_date,
            }
        )
    return [
        {**g, "total": str(g["total"])} for g in sorted(groups.values(), key=lambda g: -g["total"])
    ]


class CreateClaims(BaseModel):
    discrepancy_ids: list[int] = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/claims", status_code=201)
def create_claims(
    body: CreateClaims,
    user: User = Depends(require(Permission.MANAGE_CLAIMS)),
    db: Session = Depends(get_db),
) -> list[dict]:
    created = service.create_claims(db, body.discrepancy_ids, body.note, user)
    partners = service.partner_names(db)
    orders = _order_ids(db, [c.order_id for c in created])
    return [_claim_out(c, partners, orders) for c in created]


@router.get("/claims")
def list_claims(
    status: ClaimStatus | None = None,
    partner_id: int | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> dict:
    filters = []
    if status:
        filters.append(Claim.status == status)
    if partner_id:
        filters.append(Claim.partner_id == partner_id)
    total = db.scalar(select(func.count()).select_from(Claim).where(*filters))
    claims = db.scalars(
        select(Claim).where(*filters).order_by(Claim.id.desc()).limit(limit).offset(offset)
    ).all()
    totals = db.execute(
        select(
            Claim.status,
            func.count(),
            func.sum(func.abs(Claim.difference)),
            func.sum(Claim.recovered_amount),
        ).group_by(Claim.status)
    ).all()
    partners = service.partner_names(db)
    orders = _order_ids(db, [c.order_id for c in claims])
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_claim_out(c, partners, orders) for c in claims],
        "by_status": {
            s.value: {"count": n, "claimed": str(a or 0), "recovered": str(r or 0)}
            for s, n, a, r in totals
        },
    }


@router.get("/claims/evidence.csv")
def evidence_for_partner(
    partner_id: int,
    include_closed: bool = False,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> Response:
    filters = [Claim.partner_id == partner_id]
    if not include_closed:
        filters.append(Claim.status.in_(service.ACTIVE))
    claims = db.scalars(select(Claim).where(*filters).order_by(Claim.id)).all()
    if not claims:
        raise AppError(404, "no_claims", "This partner has no claims to export.")
    partners = service.partner_names(db)
    name = (partners.get(partner_id) or "partner").replace(" ", "-").lower()
    return _csv(service.evidence_csv(claims, partners), f"claims-{name}-{date.today():%Y%m%d}.csv")


def _csv(content: str, filename: str) -> Response:
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/claims/{claim_id}")
def claim_detail(
    claim_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> dict:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise AppError(404, "claim_not_found", "That claim doesn't exist.")
    partners = service.partner_names(db)
    events = db.execute(
        select(ClaimEvent, User.email)
        .join(User, User.id == ClaimEvent.actor_id)
        .where(ClaimEvent.claim_id == claim_id)
        .order_by(ClaimEvent.id)
    ).all()
    discrepancy = db.get(Discrepancy, claim.discrepancy_id)
    return {
        **_claim_out(claim, partners, _order_ids(db, [claim.order_id])),
        "evidence": claim.evidence,
        "calculation": service.calculation_text(claim.evidence or {}),
        "discrepancy_state": discrepancy.state.value if discrepancy else None,
        "allowed_transitions": [s.value for s in service.TRANSITIONS.get(claim.status, ())],
        "events": [
            {
                "from_status": e.from_status.value if e.from_status else None,
                "to_status": e.to_status.value,
                "amount": str(e.amount) if e.amount is not None else None,
                "note": e.note,
                "actor": email,
                "at": e.created_at,
            }
            for e, email in events
        ],
    }


class Transition(BaseModel):
    to_status: ClaimStatus
    recovered_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=2000)


@router.post("/claims/{claim_id}/transitions")
def transition(
    claim_id: int,
    body: Transition,
    user: User = Depends(require(Permission.MANAGE_CLAIMS)),
    db: Session = Depends(get_db),
) -> dict:
    service.transition(db, claim_id, body.to_status, body.recovered_amount, body.note, user)
    return claim_detail(claim_id, user, db)


@router.get("/claims/{claim_id}/evidence.csv")
def evidence_for_claim(
    claim_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> Response:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise AppError(404, "claim_not_found", "That claim doesn't exist.")
    return _csv(service.evidence_csv([claim], service.partner_names(db)), f"{claim.claim_no}.csv")
