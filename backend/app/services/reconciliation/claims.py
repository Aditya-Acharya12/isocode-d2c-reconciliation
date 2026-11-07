"""Claims: one per discrepancy, with the evidence copied in at the moment of filing."""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    Claim,
    ClaimEvent,
    Discrepancy,
    Order,
    Partner,
    ReconciliationResult,
    SalesChannel,
    UploadedFile,
    User,
)
from app.models.enums import ClaimStatus, DiscrepancyCategory, DiscrepancyState
from app.services.audit import record_audit
from app.services.reconciliation.engine import rupees

ACTIVE = (
    ClaimStatus.DRAFT,
    ClaimStatus.SUBMITTED,
    ClaimStatus.ACKNOWLEDGED,
    ClaimStatus.PARTIALLY_RECOVERED,
)
TERMINAL = (ClaimStatus.RECOVERED, ClaimStatus.REJECTED, ClaimStatus.WRITTEN_OFF)
# A discrepancy with a claim in one of these can't be claimed again. This must match the
# partial unique index on claims.discrepancy_id, which excludes rejected and written off.
BLOCKS_NEW_CLAIM = (*ACTIVE, ClaimStatus.RECOVERED)
# A claim closed with no further recovery expected.
CLOSED = (ClaimStatus.RECOVERED, ClaimStatus.WRITTEN_OFF)
TRANSITIONS: dict[ClaimStatus, tuple[ClaimStatus, ...]] = {
    ClaimStatus.DRAFT: (ClaimStatus.SUBMITTED, ClaimStatus.WRITTEN_OFF),
    ClaimStatus.SUBMITTED: (
        ClaimStatus.ACKNOWLEDGED,
        ClaimStatus.PARTIALLY_RECOVERED,
        ClaimStatus.RECOVERED,
        ClaimStatus.REJECTED,
        ClaimStatus.WRITTEN_OFF,
    ),
    ClaimStatus.ACKNOWLEDGED: (
        ClaimStatus.PARTIALLY_RECOVERED,
        ClaimStatus.RECOVERED,
        ClaimStatus.REJECTED,
        ClaimStatus.WRITTEN_OFF,
    ),
    ClaimStatus.PARTIALLY_RECOVERED: (
        ClaimStatus.PARTIALLY_RECOVERED,
        ClaimStatus.RECOVERED,
        ClaimStatus.WRITTEN_OFF,
    ),
}
STREAM_FOR = {
    DiscrepancyCategory.SHORT_PAID: "receivable",
    DiscrepancyCategory.UNPAID_PAST_DUE: "receivable",
    DiscrepancyCategory.FREIGHT_OVERCHARGED: "freight",
    DiscrepancyCategory.RETURN_NOT_RECEIVED: "returns",
}
CLAIMABLE = tuple(STREAM_FOR)


def claimed_amount(claim: Claim) -> Decimal:
    return abs(claim.difference or Decimal("0"))


# --- Exposure ------------------------------------------------------------------------
#
# How much of an open discrepancy is still at risk, once its claim is taken into account.
# docs/architecture.md section 8 describes this in words.

ZERO = Decimal("0")


def split_exposure(
    current: Decimal, claimed: Decimal, recovered: Decimal, status: ClaimStatus | None
) -> tuple[Decimal, Decimal]:
    """Returns (at risk, written off) for one open discrepancy. Neither is ever negative.

    current: the shortfall the latest run still sees. claimed: the shortfall when the claim
    was filed. recovered: the running total finance entered on the claim. status: the
    claim's status, or None when there's no claim (or only a rejected one).

    A recovery can reach the data too, as a later settlement or remittance, and then the
    run already shows a smaller shortfall. That part is counted once, not twice: only the
    recovery the data doesn't yet show is subtracted.
    """
    if status is None or status == ClaimStatus.REJECTED:
        return max(current, ZERO), ZERO
    already_in_data = max(claimed - current, ZERO)
    not_yet_in_data = max(recovered - already_in_data, ZERO)
    outstanding = max(current - not_yet_in_data, ZERO)
    if status not in CLOSED:
        return outstanding, ZERO
    # A closed claim stops the chase for what it claimed. Anything still outstanding within
    # that amount is written off. A shortfall that has grown beyond the claim stays at risk.
    written_off = min(outstanding, max(claimed - recovered, ZERO))
    return outstanding - written_off, written_off


def exposure_by_category(db: Session) -> dict[str, dict]:
    """Open discrepancies by category: count, gross shortfall, at risk, written off and
    the recovery entered on their claims."""
    out: dict[str, dict] = {}

    def bucket(category: str) -> dict:
        return out.setdefault(
            category,
            {"count": 0, "gross": ZERO, "at_risk": ZERO, "written_off": ZERO, "recovered": ZERO},
        )

    # The claim that governs each discrepancy: the one blocking a new claim if there is
    # one (at most one, by the unique index), otherwise the latest written off. A rejected
    # claim never governs: the discrepancy is back to being fully at risk.
    governing = (
        select(
            Claim.discrepancy_id,
            Claim.status,
            func.abs(Claim.difference).label("claimed"),
            Claim.recovered_amount,
        )
        .where(Claim.status != ClaimStatus.REJECTED)
        .order_by(Claim.discrepancy_id, (Claim.status == ClaimStatus.WRITTEN_OFF), Claim.id.desc())
        .distinct(Claim.discrepancy_id)
        .subquery()
    )
    # Unclaimed discrepancies are all at risk, so they're summed in the database.
    for category, count, gross in db.execute(
        select(Discrepancy.category, func.count(), func.sum(func.abs(Discrepancy.difference)))
        .outerjoin(governing, governing.c.discrepancy_id == Discrepancy.id)
        .where(Discrepancy.state == DiscrepancyState.OPEN, governing.c.discrepancy_id.is_(None))
        .group_by(Discrepancy.category)
    ):
        b = bucket(category.value)
        b["count"] += count
        b["gross"] += gross or ZERO
        b["at_risk"] += gross or ZERO
    # Claimed ones are few, so each is worked out here, by split_exposure.
    for category, difference, status, claimed, recovered in db.execute(
        select(
            Discrepancy.category,
            Discrepancy.difference,
            governing.c.status,
            governing.c.claimed,
            governing.c.recovered_amount,
        )
        .join(governing, governing.c.discrepancy_id == Discrepancy.id)
        .where(Discrepancy.state == DiscrepancyState.OPEN)
    ):
        current = abs(difference or ZERO)
        at_risk, written_off = split_exposure(
            current, claimed or ZERO, recovered or ZERO, ClaimStatus(status)
        )
        b = bucket(category.value)
        b["count"] += 1
        b["gross"] += current
        b["at_risk"] += at_risk
        b["written_off"] += written_off
        b["recovered"] += recovered or ZERO
    return out


def _paise(amount: Decimal | None) -> int | None:
    return None if amount is None else int(amount * 100)


def _only_partner_freight(stream: dict, discrepancy: Discrepancy) -> dict:
    """The freight stream limited to the claimed courier's own invoice lines.

    An order can be billed by more than one courier, such as a forward shipment by one and
    the RTO by another. A claim goes to one courier, so another's invoice numbers and
    amounts must not appear in it. The stream's totals cover every courier, so they're
    replaced by this discrepancy's own figures.
    """
    partner_id = discrepancy.counterparty_partner_id
    return {
        **stream,
        "lines": [line for line in stream.get("lines", []) if line.get("partner_id") == partner_id],
        "expected_paise": _paise(discrepancy.expected),
        "actual_paise": _paise(discrepancy.actual),
    }


def _evidence(db: Session, discrepancy: Discrepancy) -> dict:
    order = db.get(Order, discrepancy.order_id)
    channel = db.get(SalesChannel, order.channel_id)
    result = db.scalar(
        select(ReconciliationResult).where(
            ReconciliationResult.order_id == order.id, ReconciliationResult.is_current
        )
    )
    stream = (
        (result.explanation or {}).get("streams", {}).get(STREAM_FOR[discrepancy.category], {})
        if result
        else {}
    )
    if discrepancy.category == DiscrepancyCategory.FREIGHT_OVERCHARGED:
        stream = _only_partner_freight(stream, discrepancy)
    records = (
        stream.get("records")
        or stream.get("lines")
        or stream.get("return_records", []) + stream.get("receipts", [])
    )
    file_ids = {order.source_file_id, *(r.get("file_id") for r in records if r.get("file_id"))}
    files = {
        f.id: f.filename
        for f in db.scalars(select(UploadedFile).where(UploadedFile.id.in_(file_ids)))
    }
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "run_id": result.run_id if result else None,
        "order": {
            "id": order.id,
            "external_id": order.external_order_id,
            "channel": channel.name,
            "order_date": order.order_date.isoformat(),
            "value": str(order.order_value),
            "payment_method": order.payment_method.value,
            "delivery_status": order.delivery_status.value,
            "tracking_id": order.tracking_id,
            "source_file": files.get(order.source_file_id),
            "source_row": order.source_row_number,
        },
        "stream": STREAM_FOR[discrepancy.category],
        "calculation": stream,
        "records": [{**r, "source_file": files.get(r.get("file_id"))} for r in records],
    }


def eligible(db: Session) -> list[Discrepancy]:
    # A recovered claim blocks a new one too, as the database index does. Only a rejected
    # or written-off claim frees the discrepancy to be claimed again.
    has_claim = select(Claim.discrepancy_id).where(Claim.status.in_(BLOCKS_NEW_CLAIM))
    return db.scalars(
        select(Discrepancy)
        .where(
            Discrepancy.state == DiscrepancyState.OPEN,
            Discrepancy.category.in_(CLAIMABLE),
            Discrepancy.id.not_in(has_claim),
        )
        .order_by(Discrepancy.counterparty_partner_id, Discrepancy.difference)
    ).all()


def create_claims(
    db: Session, discrepancy_ids: list[int], note: str | None, user: User
) -> list[Claim]:
    if not discrepancy_ids:
        raise AppError(422, "nothing_selected", "Select at least one discrepancy.")
    discrepancies = db.scalars(
        select(Discrepancy).where(Discrepancy.id.in_(discrepancy_ids)).with_for_update()
    ).all()
    if len(discrepancies) != len(set(discrepancy_ids)):
        raise AppError(404, "discrepancy_not_found", "Some of those discrepancies don't exist.")
    eligible_ids = {d.id for d in eligible(db)}
    refused = [d.id for d in discrepancies if d.id not in eligible_ids]
    if refused:
        raise AppError(
            409,
            "not_claimable",
            "Some selected discrepancies can't be claimed: they're resolved, need review, "
            "or already have a claim in progress or recovered.",
            {"discrepancy_ids": refused},
        )
    claims = []
    for discrepancy in discrepancies:
        claim = Claim(
            claim_no=f"PENDING-{uuid4().hex[:20]}",
            discrepancy_id=discrepancy.id,
            partner_id=discrepancy.counterparty_partner_id,
            order_id=discrepancy.order_id,
            category=discrepancy.category,
            expected=discrepancy.expected,
            actual=discrepancy.actual,
            difference=discrepancy.difference,
            status=ClaimStatus.DRAFT,
            resolution_notes=None,
            evidence=_evidence(db, discrepancy),
            created_by=user.id,
        )
        db.add(claim)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise AppError(
                409, "claim_exists", "A claim was just created for one of these."
            ) from exc
        claim.claim_no = f"CLM-{claim.id:06d}"
        db.add(
            ClaimEvent(
                claim_id=claim.id,
                from_status=None,
                to_status=ClaimStatus.DRAFT,
                amount=claimed_amount(claim),
                note=note,
                actor_id=user.id,
            )
        )
        record_audit(
            db,
            actor_id=user.id,
            action="claim.create",
            entity_type="claim",
            entity_id=claim.id,
            after={
                "claim_no": claim.claim_no,
                "discrepancy_id": discrepancy.id,
                "amount": str(claimed_amount(claim)),
            },
        )
        claims.append(claim)
    db.commit()
    return claims


def transition(
    db: Session,
    claim_id: int,
    to_status: ClaimStatus,
    recovered: Decimal | None,
    note: str | None,
    user: User,
) -> Claim:
    claim = db.get(Claim, claim_id, with_for_update=True)
    if claim is None:
        raise AppError(404, "claim_not_found", "That claim doesn't exist.")
    allowed = TRANSITIONS.get(claim.status, ())
    if to_status not in allowed:
        raise AppError(
            409,
            "invalid_transition",
            f"A {claim.status.value.replace('_', ' ')} claim can't become "
            f"{to_status.value.replace('_', ' ')}.",
        )
    amount = claimed_amount(claim)
    if to_status == ClaimStatus.RECOVERED:
        recovered = amount if recovered is None else recovered
    if to_status in (ClaimStatus.PARTIALLY_RECOVERED, ClaimStatus.RECOVERED):
        if recovered is None:
            raise AppError(422, "amount_required", "Enter the total amount recovered so far.")
        if recovered < claim.recovered_amount:
            raise AppError(
                422,
                "amount_decreased",
                "The total recovered can't go down. Enter the new running total.",
            )
        if recovered > amount:
            raise AppError(
                422,
                "amount_too_large",
                f"That's more than the {rupees(int(amount * 100))} claimed.",
            )
        if to_status == ClaimStatus.PARTIALLY_RECOVERED and recovered >= amount:
            raise AppError(
                422, "use_recovered", "That's the full amount. Mark it recovered instead."
            )
    if to_status in (ClaimStatus.REJECTED, ClaimStatus.WRITTEN_OFF) and not (note or "").strip():
        raise AppError(422, "note_required", "Add a note explaining the outcome.")
    before = {"status": claim.status.value, "recovered": str(claim.recovered_amount)}
    db.add(
        ClaimEvent(
            claim_id=claim.id,
            from_status=claim.status,
            to_status=to_status,
            amount=recovered,
            note=note,
            actor_id=user.id,
        )
    )
    claim.status = to_status
    if recovered is not None:
        claim.recovered_amount = recovered
    if note and to_status in TERMINAL:
        claim.resolution_notes = note
    record_audit(
        db,
        actor_id=user.id,
        action="claim.transition",
        entity_type="claim",
        entity_id=claim.id,
        before=before,
        after={"status": to_status.value, "recovered": str(claim.recovered_amount)},
    )
    db.commit()
    return claim


# --- Evidence export -----------------------------------------------------------------

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _safe(value) -> str:
    text = "" if value is None else str(value)
    # Partner data may end up in a spreadsheet. A leading quote stops it running as a formula.
    return f"'{text}" if text.startswith(_FORMULA) and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except ArithmeticError:
        return False
    except ValueError:
        return False


def calculation_text(evidence: dict) -> str:
    stream, calc = evidence.get("stream"), evidence.get("calculation") or {}
    if stream == "receivable" and calc.get("rule"):
        rule = calc["rule"]
        pct = rule["pct_bps"] / 100
        parts = [f"Order value {rupees(calc['base_paise'])}"]
        if pct:
            parts.append(f"less {pct:g}%")
        if rule["fixed_paise"]:
            parts.append(f"less {rupees(rule['fixed_paise'])} fixed")
        text_ = (
            f"{' '.join(parts)} ({rule['rate_card']}) = {rupees(calc['expected_paise'])} "
            f"expected. Received {rupees(calc['net_received_paise'])} net"
        )
        if calc.get("tcs_paise") or calc.get("tds_paise"):
            text_ += (
                f" + {rupees(calc['tcs_paise'])} TCS + {rupees(calc['tds_paise'])} TDS "
                f"withheld = {rupees(calc['actual_paise'])}"
            )
        if calc.get("due_date"):
            text_ += f". Due {calc['due_date']}"
        return text_ + f". {calc.get('reason', '')}"
    if stream == "freight":
        lines = [
            f"{line.get('reference')} {line['extra_type'] if 'extra_type' in line else line.get('shipment_type', '')}: "  # noqa: E501
            f"billed {rupees(line.get('billed_excl_gst_paise'))} excl. GST against "
            f"{rupees(line.get('expected_paise'))} for slab {line.get('slab_used')} "
            f"({line.get('rule', {}).get('rate_card', 'no rule')})"
            for line in calc.get("lines", [])
            if line.get("expected_paise") is not None
        ]
        return "; ".join(lines) + f". {calc.get('reason', '')}"
    if stream == "returns":
        return (
            f"Returned {calc.get('returned_at')}. Grace period {calc.get('grace_days')} days, "
            f"until {calc.get('due_date')}. No warehouse receipt. "
            f"Value at risk {rupees(calc.get('value_at_risk_paise'))}."
        )
    return calc.get("reason", "")


CSV_HEADER = [
    "claim_no",
    "partner",
    "status",
    "order_id",
    "channel",
    "order_date",
    "order_value",
    "category",
    "expected",
    "actual",
    "difference",
    "claimed",
    "recovered",
    "calculation",
    "evidence_type",
    "evidence_reference",
    "evidence_date",
    "evidence_amount",
    "source_file",
    "source_row",
]


def evidence_csv(claims: list[Claim], partners: dict[int, str]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for claim in claims:
        evidence = claim.evidence or {}
        order = evidence.get("order", {})
        base = [
            claim.claim_no,
            partners.get(claim.partner_id),
            claim.status.value,
            order.get("external_id"),
            order.get("channel"),
            order.get("order_date"),
            order.get("value"),
            claim.category.value,
            claim.expected,
            claim.actual,
            claim.difference,
            claimed_amount(claim),
            claim.recovered_amount,
            calculation_text(evidence),
        ]
        # The order itself, then every partner record behind the figures.
        writer.writerow(
            [
                _safe(v)
                for v in [
                    *base,
                    "order",
                    order.get("external_id"),
                    order.get("order_date"),
                    order.get("value"),
                    order.get("source_file"),
                    order.get("source_row"),
                ]
            ]
        )
        for record in evidence.get("records", []):
            amount = record.get("amount_paise")
            writer.writerow(
                [
                    _safe(v)
                    for v in [
                        *base,
                        record.get("kind"),
                        record.get("reference"),
                        record.get("date"),
                        f"{amount / 100:.2f}" if amount is not None else "",
                        record.get("source_file"),
                        record.get("row"),
                    ]
                ]
            )
    return buffer.getvalue()


def partner_names(db: Session) -> dict[int, str]:
    return {p.id: p.name for p in db.scalars(select(Partner))}
