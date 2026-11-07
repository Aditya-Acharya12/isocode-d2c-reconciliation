"""Reconciliation, returns, tax and rate-card views, and starting runs."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models import (
    BackgroundJob,
    Claim,
    Discrepancy,
    FreightRateRule,
    Order,
    Partner,
    RateCard,
    ReconciliationResult,
    ReconciliationRun,
    SalesChannel,
    SettlementRateRule,
    TaxRule,
    UploadedFile,
    User,
)
from app.models.enums import DiscrepancyState, JobKind, JobStatus, ReconStatus
from app.services.audit import record_audit
from app.services.jobs import worker
from app.services.reconciliation.claims import ACTIVE
from app.services.reconciliation.runner import TRANSACTION_TABLES

router = APIRouter(tags=["reconciliation"])
CASH = ("short_paid", "unpaid_past_due", "freight_overcharged")
TAX_NOTE = (
    "Reconciliation estimates only, not tax advice. Leakage figures use the TCS and TDS that "
    "partners report, and the configured rates below only flag figures that look unusual. "
    "Confirm rates and treatment with your chartered accountant."
)


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _run_out(run: ReconciliationRun, job: BackgroundJob | None) -> dict:
    return {
        "id": run.id,
        "as_of_date": run.as_of_date,
        "status": run.status.value,
        "counts": run.counts,
        "timings_ms": run.timings_ms,
        "created_at": run.created_at,
        "finished_at": job.finished_at if job else None,
        "error": job.error if job else None,
    }


class RunRequest(BaseModel):
    as_of_date: date | None = None


@router.post("/reconciliation/runs", status_code=202)
def start_run(
    body: RunRequest,
    user: User = Depends(require(Permission.OPERATE_RECONCILIATION)),
    db: Session = Depends(get_db),
) -> dict:
    active = db.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
        .order_by(ReconciliationRun.id.desc())
    )
    if active is not None:
        # A second click while one is running returns the same run.
        return {
            **_run_out(active, db.get(BackgroundJob, active.background_job_id)),
            "created": False,
        }
    as_of = body.as_of_date or _today()
    if as_of > _today():
        raise AppError(422, "future_date", "The as-of date can't be in the future.")
    job = BackgroundJob(
        kind=JobKind.RECONCILIATION,
        payload={},
        created_by=user.id,
        idempotency_key=f"reconcile:{uuid4().hex}",
    )
    db.add(job)
    db.flush()
    run = ReconciliationRun(
        as_of_date=as_of,
        scope={"orders": "all"},
        background_job_id=job.id,
        triggered_by=user.id,
        rate_card_ids=[],
        settings_snapshot={},
    )
    db.add(run)
    db.flush()
    job.payload = {"run_id": run.id}
    record_audit(
        db,
        actor_id=user.id,
        action="reconciliation.start",
        entity_type="reconciliation_run",
        entity_id=run.id,
        after={"as_of": as_of.isoformat()},
    )
    db.commit()
    worker.notify()
    return {**_run_out(run, job), "created": True}


@router.get("/reconciliation/runs")
def list_runs(
    limit: int = Query(default=10, ge=1, le=50),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(ReconciliationRun, BackgroundJob)
        .join(BackgroundJob, BackgroundJob.id == ReconciliationRun.background_job_id)
        .order_by(ReconciliationRun.id.desc())
        .limit(limit)
    ).all()
    return [_run_out(run, job) for run, job in rows]


@router.get("/reconciliation/summary")
def summary(
    _: User = Depends(require(Permission.VIEW_WORKSPACE)), db: Session = Depends(get_db)
) -> dict:
    statuses = dict(
        db.execute(
            select(ReconciliationResult.primary_status, func.count())
            .where(ReconciliationResult.is_current)
            .group_by(ReconciliationResult.primary_status)
        ).all()
    )
    open_by_category = {
        category.value: {"count": count, "amount": str(-(amount or 0))}
        for category, count, amount in db.execute(
            select(Discrepancy.category, func.count(), func.sum(Discrepancy.difference))
            .where(Discrepancy.state == DiscrepancyState.OPEN)
            .group_by(Discrepancy.category)
        ).all()
    }
    recovered = db.scalar(
        select(func.coalesce(func.sum(Claim.recovered_amount), 0))
        .join(Discrepancy, Discrepancy.id == Claim.discrepancy_id)
        .where(Discrepancy.state == DiscrepancyState.OPEN, Claim.status.in_(ACTIVE))
    ) or Decimal("0")
    cash = sum(Decimal(open_by_category.get(c, {}).get("amount", "0")) for c in CASH)
    last = db.execute(
        select(ReconciliationRun, BackgroundJob)
        .join(BackgroundJob, BackgroundJob.id == ReconciliationRun.background_job_id)
        .order_by(ReconciliationRun.id.desc())
        .limit(1)
    ).first()
    unmatched = {
        kind: db.scalar(text(f"SELECT count(*) FROM {table} WHERE order_id IS NULL"))  # noqa: S608
        for kind, table in TRANSACTION_TABLES.items()
    }
    return {
        "orders_total": db.scalar(select(func.count()).select_from(Order)),
        "orders_evaluated": sum(statuses.values()),
        "statuses": {status.value: statuses.get(status, 0) for status in ReconStatus},
        "reconciled": statuses.get(ReconStatus.SETTLED, 0),
        "open_discrepancies": sum(v["count"] for v in open_by_category.values()),
        "open_by_category": open_by_category,
        "money_at_risk": str(cash - recovered),
        "recovered_on_open": str(recovered),
        "inventory_at_risk": open_by_category.get("return_not_received", {}).get("amount", "0"),
        "unmatched_records": unmatched,
        "last_run": _run_out(*last) if last else None,
    }


@router.get("/reconciliation/results")
def results(
    status: ReconStatus | None = None,
    channel_id: int | None = None,
    partner_id: int | None = None,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=64),
    sort: str = Query(default="date", pattern="^(date|difference)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> dict:
    filters = ["r.is_current"]
    params: dict = {"limit": limit, "offset": offset}
    if status:
        filters.append("r.primary_status = :status")
        params["status"] = status.value
    if channel_id:
        filters.append("o.channel_id = :channel")
        params["channel"] = channel_id
    if partner_id:
        filters.append("""(o.courier_id = :partner OR c.partner_id = :partner OR EXISTS (
            SELECT 1 FROM discrepancies d2 WHERE d2.order_id = o.id
            AND d2.counterparty_partner_id = :partner))""")
        params["partner"] = partner_id
    if category:
        filters.append("""EXISTS (SELECT 1 FROM discrepancies d3 WHERE d3.order_id = o.id
            AND d3.state = 'open' AND d3.category = :category)""")
        params["category"] = category
    if date_from:
        filters.append("o.order_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("o.order_date <= :date_to")
        params["date_to"] = date_to
    if q:
        filters.append("(o.external_order_id ILIKE :q OR o.tracking_id ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    where = " AND ".join(filters)
    base = f"""
        FROM reconciliation_results r
        JOIN orders o ON o.id = r.order_id
        JOIN sales_channels c ON c.id = o.channel_id
        WHERE {where}
    """  # noqa: S608 - filters are fixed strings with bound parameters
    total = db.scalar(text(f"SELECT count(*) {base}"), params)
    order_by = (
        "abs(coalesce(r.actual_paise, 0) - coalesce(r.expected_paise, 0)) DESC, o.id"
        if sort == "difference"
        else "o.order_date DESC, o.id DESC"
    )
    rows = db.execute(
        text(f"""
        SELECT o.id, o.external_order_id, c.name AS channel, o.order_date, o.order_value,
               o.payment_method, o.delivery_status, r.primary_status, r.expected_paise,
               r.actual_paise, r.explanation->>'reason' AS reason,
               (SELECT array_agg(DISTINCT d.category::text) FROM discrepancies d
                WHERE d.order_id = o.id AND d.state = 'open') AS categories
        {base} ORDER BY {order_by} LIMIT :limit OFFSET :offset
    """),
        params,
    ).all()  # noqa: S608
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "order_id": r.id,
                "external_order_id": r.external_order_id,
                "channel": r.channel,
                "order_date": r.order_date,
                "order_value": str(r.order_value),
                "payment_method": r.payment_method,
                "delivery_status": r.delivery_status,
                "status": r.primary_status,
                "expected_paise": r.expected_paise,
                "actual_paise": r.actual_paise,
                "difference_paise": (r.actual_paise - r.expected_paise)
                if r.actual_paise is not None and r.expected_paise is not None
                else None,
                "reason": r.reason,
                "open_categories": r.categories or [],
            }
            for r in rows
        ],
    }


def _source_records(db: Session, order_id: int) -> list[dict]:
    records = []
    queries = {
        "settlement": "settlement_ref AS reference, settlement_date AS record_date, net_amount AS amount",
        "cod_remittance": "remittance_ref AS reference, remittance_date AS record_date, net_remittance AS amount",
        "freight": "invoice_no AS reference, invoice_date AS record_date, total_charge AS amount",
        "return": "return_type AS reference, returned_at AS record_date, return_value AS amount",
        "warehouse_receipt": "receipt_ref AS reference, received_at AS record_date, NULL::numeric AS amount",
    }
    for kind, table in TRANSACTION_TABLES.items():
        rows = db.execute(
            text(f"""
            SELECT t.id, t.partner_id, p.name AS partner, t.source_file_id, f.filename,
                   t.source_row_number, t.match_method, t.tracking_id, {queries[kind]}
            FROM {table} t JOIN partners p ON p.id = t.partner_id
            JOIN uploaded_files f ON f.id = t.source_file_id
            WHERE t.order_id = :order ORDER BY t.id
        """),
            {"order": order_id},
        ).all()  # noqa: S608
        records += [
            {
                "kind": kind,
                "id": r.id,
                "partner": r.partner,
                "file_id": r.source_file_id,
                "filename": r.filename,
                "row": r.source_row_number,
                "match_method": r.match_method,
                "reference": r.reference,
                "date": r.record_date,
                "amount": str(r.amount) if r.amount is not None else None,
                "tracking_id": r.tracking_id,
            }
            for r in rows
        ]
    return records


@router.get("/reconciliation/orders/{order_id}")
def order_detail(
    order_id: int,
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> dict:
    order = db.get(Order, order_id)
    if order is None:
        raise AppError(404, "order_not_found", "That order doesn't exist.")
    channel = db.get(SalesChannel, order.channel_id)
    file = db.get(UploadedFile, order.source_file_id)
    result = db.scalar(
        select(ReconciliationResult).where(
            ReconciliationResult.order_id == order_id, ReconciliationResult.is_current
        )
    )
    run = db.get(ReconciliationRun, result.run_id) if result else None
    partners = {p.id: p.name for p in db.scalars(select(Partner))}
    discrepancies = db.scalars(
        select(Discrepancy).where(Discrepancy.order_id == order_id).order_by(Discrepancy.id)
    ).all()
    claims = {
        c.discrepancy_id: c
        for c in db.scalars(select(Claim).where(Claim.order_id == order_id).order_by(Claim.id))
    }
    return {
        "order": {
            "id": order.id,
            "external_order_id": order.external_order_id,
            "channel": channel.name,
            "order_date": order.order_date,
            "order_value": str(order.order_value),
            "payment_method": order.payment_method.value,
            "delivery_status": order.delivery_status.value,
            "tracking_id": order.tracking_id,
            "courier": partners.get(order.courier_id),
            "delivered_at": order.delivered_at,
            "returned_at": order.returned_at,
            "weight_slab": order.weight_slab,
            "zone": order.zone,
            "quantity": order.quantity,
            "file_id": order.source_file_id,
            "filename": file.filename if file else None,
            "row": order.source_row_number,
        },
        "result": {
            "status": result.primary_status.value,
            "expected_paise": result.expected_paise,
            "actual_paise": result.actual_paise,
            "tcs_paise": result.tcs_paise,
            "tds_paise": result.tds_paise,
            "explanation": result.explanation,
            "run_id": result.run_id,
            "as_of_date": run.as_of_date if run else None,
        }
        if result
        else None,
        "discrepancies": [
            {
                "id": d.id,
                "category": d.category.value,
                "partner": partners.get(d.counterparty_partner_id),
                "expected": str(d.expected),
                "actual": str(d.actual),
                "difference": str(d.difference),
                "due_date": d.due_date,
                "state": d.state.value,
                "claim": {
                    "id": claims[d.id].id,
                    "claim_no": claims[d.id].claim_no,
                    "status": claims[d.id].status.value,
                }
                if d.id in claims
                else None,
            }
            for d in discrepancies
        ],
        "records": _source_records(db, order_id),
        "partners": {str(k): v for k, v in partners.items()},
    }


@router.get("/reconciliation/returns")
def returns_view(
    state: str = Query(default="all", pattern="^(all|awaiting|received|not_received|review)$"),
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> dict:
    status_for = {
        "awaiting": "pending",
        "received": "settled",
        "not_received": "return_not_received",
        "review": "needs_review",
    }
    filters = ["r.is_current", "r.explanation->'streams' ? 'returns'"]
    params: dict = {"limit": limit, "offset": offset}
    if state != "all":
        filters.append("r.explanation->'streams'->'returns'->>'status' = :state")
        params["state"] = status_for[state]
    if q:
        filters.append("(o.external_order_id ILIKE :q OR o.tracking_id ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    base = f"""FROM reconciliation_results r JOIN orders o ON o.id = r.order_id
               JOIN sales_channels c ON c.id = o.channel_id WHERE {" AND ".join(filters)}"""  # noqa: S608
    total = db.scalar(text(f"SELECT count(*) {base}"), params)
    counts = dict(
        db.execute(
            text("""
        SELECT r.explanation->'streams'->'returns'->>'status', count(*)
        FROM reconciliation_results r WHERE r.is_current AND r.explanation->'streams' ? 'returns'
        GROUP BY 1
    """)
        ).all()
    )
    rows = db.execute(
        text(f"""
        SELECT o.id, o.external_order_id, c.name AS channel, o.delivery_status, o.order_value,
               o.tracking_id, r.explanation->'streams'->'returns' AS stream
        {base} ORDER BY (r.explanation->'streams'->'returns'->>'returned_at') NULLS LAST, o.id
        LIMIT :limit OFFSET :offset
    """),
        params,
    ).all()  # noqa: S608
    partners = {p.id: p.name for p in db.scalars(select(Partner))}
    items = []
    for r in rows:
        stream = r.stream or {}
        returns, receipts = stream.get("return_records", []), stream.get("receipts", [])
        items.append(
            {
                "order_id": r.id,
                "external_order_id": r.external_order_id,
                "channel": r.channel,
                "delivery_status": r.delivery_status,
                "tracking_id": r.tracking_id,
                "return_status": (returns[0].get("return_status") if returns else None)
                or r.delivery_status,
                "return_partner": partners.get(returns[0]["partner_id"]) if returns else None,
                "returned_at": stream.get("returned_at"),
                "days_since_return": stream.get("days_since_return"),
                "grace_days": stream.get("grace_days"),
                "due_date": stream.get("due_date"),
                "received": bool(receipts),
                "received_at": receipts[0]["date"] if receipts else None,
                "receipt_reference": receipts[0]["reference"] if receipts else None,
                "outcome": stream.get("status"),
                "reason": stream.get("reason"),
                "value_at_risk": str(r.order_value)
                if stream.get("status") == "return_not_received"
                else "0",
                "evidence_files": sorted({x["file_id"] for x in returns + receipts}),
            }
        )
    return {"total": total, "limit": limit, "offset": offset, "counts": counts, "items": items}


@router.get("/reconciliation/unmatched")
def unmatched(
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require(Permission.VIEW_WORKSPACE)),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Records that couldn't be linked to exactly one order, for a person to review."""
    out = []
    for kind, table in TRANSACTION_TABLES.items():
        rows = db.execute(
            text(f"""
            SELECT t.id, p.name AS partner, t.source_file_id, f.filename, t.source_row_number,
                   t.external_order_ref, t.tracking_id,
                   (SELECT count(*) FROM orders o WHERE o.external_order_id = t.external_order_ref
                      OR o.tracking_id = t.tracking_id) AS candidates
            FROM {table} t JOIN partners p ON p.id = t.partner_id
            JOIN uploaded_files f ON f.id = t.source_file_id
            WHERE t.order_id IS NULL ORDER BY t.id LIMIT :limit
        """),
            {"limit": limit},
        ).all()  # noqa: S608
        out += [
            {
                "kind": kind,
                "id": r.id,
                "partner": r.partner,
                "file_id": r.source_file_id,
                "filename": r.filename,
                "row": r.source_row_number,
                "order_ref": r.external_order_ref,
                "tracking_id": r.tracking_id,
                "reason": "Matches more than one order"
                if r.candidates > 1
                else "No order has this order ID or tracking number",
            }
            for r in rows
        ]
    return out[:limit]


@router.get("/reconciliation/tax")
def tax_summary(
    _: User = Depends(require(Permission.VIEW_WORKSPACE)), db: Session = Depends(get_db)
) -> dict:
    rows = db.execute(
        text("""
        SELECT p.name AS partner, count(*) AS lines,
               coalesce(sum(s.tcs_amount), 0) AS tcs, coalesce(sum(s.tds_amount), 0) AS tds,
               count(*) FILTER (WHERE s.tcs_amount IS NULL AND s.tds_amount IS NULL) AS unreported,
               coalesce(sum(s.gross_amount), 0) AS gross
        FROM settlement_lines s JOIN partners p ON p.id = s.partner_id
        GROUP BY p.name ORDER BY p.name
    """)
    ).all()
    rules = db.execute(
        select(SalesChannel.name, TaxRule.kind, TaxRule.rate_bps, TaxRule.effective_from)
        .join(SalesChannel, SalesChannel.id == TaxRule.channel_id)
        .order_by(SalesChannel.name)
    ).all()
    return {
        "note": TAX_NOTE,
        "partners": [
            {
                "partner": r.partner,
                "settlement_lines": r.lines,
                "tcs": str(r.tcs),
                "tds": str(r.tds),
                "lines_without_tax_reported": r.unreported,
            }
            for r in rows
        ],
        "configured_rates": [
            {
                "channel": channel,
                "kind": kind.value,
                "rate_percent": bps / 100,
                "effective_from": start,
            }
            for channel, kind, bps, start in rules
        ],
    }


@router.get("/rate-cards")
def rate_cards(
    _: User = Depends(require(Permission.VIEW_CONFIG)), db: Session = Depends(get_db)
) -> list[dict]:
    channels = {c.id: c.name for c in db.scalars(select(SalesChannel))}
    partners = {p.id: p.name for p in db.scalars(select(Partner))}
    out = []
    for card in db.scalars(select(RateCard).order_by(RateCard.name, RateCard.version)):
        settlement = db.scalars(
            select(SettlementRateRule)
            .where(SettlementRateRule.rate_card_id == card.id)
            .order_by(SettlementRateRule.channel_id, SettlementRateRule.id)
        ).all()
        freight = db.scalars(
            select(FreightRateRule)
            .where(FreightRateRule.rate_card_id == card.id)
            .order_by(FreightRateRule.courier_id, FreightRateRule.id)
        ).all()
        out.append(
            {
                "id": card.id,
                "name": card.name,
                "version": card.version,
                "status": card.status.value,
                "effective_from": card.effective_from,
                "effective_to": card.effective_to,
                "settlement_rules": [
                    {
                        "channel": channels.get(r.channel_id),
                        "payment_method": r.payment_method.value,
                        "event": r.event.value,
                        "payer": partners.get(r.payer_partner_id, "Order's courier"),
                        "percent": r.pct_bps / 100,
                        "fixed": r.fixed_paise / 100,
                        "round_to": r.round_to_paise / 100,
                    }
                    for r in settlement
                ],
                "freight_rules": [
                    {
                        "courier": partners.get(r.courier_id),
                        "weight_slab": r.weight_slab,
                        "zone": r.zone or "Any",
                        "shipment_type": r.shipment_type.value,
                        "base": r.base_paise / 100,
                        "cod_percent": r.cod_pct_bps / 100,
                        "cod_minimum": r.cod_min_paise / 100,
                        "fuel_percent": r.fuel_pct_bps / 100,
                    }
                    for r in freight
                ],
            }
        )
    return out
