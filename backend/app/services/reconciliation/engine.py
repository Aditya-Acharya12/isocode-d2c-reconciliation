"""The deterministic reconciliation rules. Pure functions: no database and no I/O.

Every amount is an integer number of paise, and every commercial rule comes from the
rate card passed in. Nothing here is decided by AI, and nothing is hardcoded about any
partner. When the inputs can't support a confident answer, the result is Needs review.

Three streams are evaluated separately for each order, because each is owed by a
different party (docs/architecture.md section 7):
- receivable: the payment for the order, by settlement or COD remittance
- freight: what the courier billed, against the rate card
- returns: whether a returned order was scanned back into the warehouse
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models.enums import DeliveryStatus, DiscrepancyCategory, ReconStatus, ShipmentType

# The order in which a status wins when an order has more than one problem.
PRECEDENCE = (
    ReconStatus.UNPAID_PAST_DUE,
    ReconStatus.RETURN_NOT_RECEIVED,
    ReconStatus.SHORT_PAID,
    ReconStatus.FREIGHT_OVERCHARGED,
    ReconStatus.NEEDS_REVIEW,
    ReconStatus.PENDING,
    ReconStatus.SETTLED,
)
RETURN_STATUSES = (DeliveryStatus.RTO, DeliveryStatus.CUSTOMER_RETURN)
EVENT_FOR_STATUS = {
    DeliveryStatus.DELIVERED: "delivered",
    DeliveryStatus.RTO: "rto",
    DeliveryStatus.CUSTOMER_RETURN: "customer_return",
}
DAMAGED = ("damaged", "damage", "defective", "missing", "short", "rejected")


# --- Inputs --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRecord:
    """One imported row that contributed to a result, traceable to its file and row."""

    kind: str
    id: int
    partner_id: int
    file_id: int
    row: int
    reference: str | None
    record_date: date | None
    amount_paise: int | None = None
    tcs_paise: int | None = None
    tds_paise: int | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "partner_id": self.partner_id,
            "file_id": self.file_id,
            "row": self.row,
            "reference": self.reference,
            "date": self.record_date.isoformat() if self.record_date else None,
            "amount_paise": self.amount_paise,
            "tcs_paise": self.tcs_paise,
            "tds_paise": self.tds_paise,
            **self.extra,
        }


@dataclass(frozen=True)
class OrderFacts:
    id: int
    external_id: str
    channel_id: int
    order_date: date
    value_paise: int
    payment_method: str
    delivery_status: str
    delivered_at: date | None
    returned_at: date | None
    courier_id: int | None
    weight_slab: str | None
    zone: str | None
    quantity: int | None
    file_id: int
    row: int


@dataclass
class OrderRecords:
    settlements: list[SourceRecord] = field(default_factory=list)
    remittances: list[SourceRecord] = field(default_factory=list)
    freight: list[SourceRecord] = field(default_factory=list)
    returns: list[SourceRecord] = field(default_factory=list)
    receipts: list[SourceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class SettlementRule:
    id: int
    rate_card: str
    channel_id: int
    payment_method: str
    event: str
    payer_partner_id: int | None
    pct_bps: int
    fixed_paise: int
    round_to_paise: int


@dataclass(frozen=True)
class FreightRule:
    id: int
    rate_card: str
    courier_id: int
    weight_slab: str
    zone: str | None
    shipment_type: str
    base_paise: int
    cod_pct_bps: int
    cod_min_paise: int
    fuel_pct_bps: int


@dataclass(frozen=True)
class Partner:
    id: int
    name: str
    settlement_terms_days: int
    return_grace_days: int


@dataclass
class Context:
    """Everything the rules need besides the order itself."""

    as_of: date
    threshold_paise: int
    partners: dict[int, Partner]
    # Rules from the rate cards in force on each order's date: looked up per order.
    settlement_rules: list[SettlementRule]
    freight_rules: list[FreightRule]
    # Channels whose partners normally withhold GST TCS or income tax TDS.
    tax_withholding_channels: set[int] = field(default_factory=set)


# --- Outputs -------------------------------------------------------------------------


@dataclass
class DiscrepancyOut:
    category: DiscrepancyCategory
    counterparty_partner_id: int
    expected_paise: int
    actual_paise: int
    difference_paise: int
    due_date: date | None


@dataclass
class StreamOutcome:
    status: ReconStatus
    expected_paise: int | None = None
    actual_paise: int | None = None
    explanation: dict = field(default_factory=dict)
    discrepancy: DiscrepancyOut | None = None


@dataclass
class Evaluation:
    primary_status: ReconStatus
    expected_paise: int | None
    actual_paise: int | None
    tcs_paise: int | None
    tds_paise: int | None
    discrepancies: list[DiscrepancyOut]
    explanation: dict


# --- Arithmetic ----------------------------------------------------------------------


def round_half_up(value_x10000: int, step_paise: int) -> int:
    """Rounds an amount held in ten-thousandths of a paisa to a multiple of step_paise."""
    step = step_paise * 10000
    sign = -1 if value_x10000 < 0 else 1
    return sign * ((abs(value_x10000) + step // 2) // step) * step_paise


def percent_of(base_paise: int, bps: int) -> int:
    """A percentage in basis points, kept exact as ten-thousandths of a paisa."""
    return base_paise * bps


def rupees(paise: int | None) -> str:
    if paise is None:
        return "-"
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{abs(paise) // 100:,}.{abs(paise) % 100:02d}"


def _sum(records: list[SourceRecord], attribute: str = "amount_paise") -> int:
    return sum(getattr(record, attribute) or 0 for record in records)


# --- Rule lookup ---------------------------------------------------------------------


def find_settlement_rule(
    order: OrderFacts, event: str, rules: list[SettlementRule]
) -> tuple[SettlementRule | None, str | None]:
    matches = [
        rule
        for rule in rules
        if rule.channel_id == order.channel_id
        and rule.payment_method == order.payment_method
        and rule.event == event
    ]
    if not matches:
        return None, "No rate card rule covers this channel, payment method and event."
    if len({rule.id for rule in matches}) > 1:
        return None, "More than one rate card rule applies on this date, so it's ambiguous."
    return matches[0], None


def find_freight_rule(
    courier_id: int,
    slab: str | None,
    zone: str | None,
    shipment_type: str,
    rules: list[FreightRule],
) -> tuple[FreightRule | None, str | None]:
    if not slab:
        return None, "There's no weight slab, so the expected freight can't be worked out."
    candidates = [
        rule
        for rule in rules
        if rule.courier_id == courier_id
        and rule.weight_slab.strip().casefold() == slab.strip().casefold()
        and rule.shipment_type == shipment_type
    ]
    # A rule for the exact zone is more specific than one for any zone.
    exact = [
        rule
        for rule in candidates
        if zone and rule.zone and rule.zone.casefold() == zone.casefold()
    ]
    general = [rule for rule in candidates if rule.zone is None]
    chosen = exact or general
    if not chosen:
        return None, f"No freight rule covers slab {slab} for a {shipment_type} shipment."
    if len(chosen) > 1:
        return None, "More than one freight rule applies, so it's ambiguous."
    return chosen[0], None


def find_rules_for_date(rules, order_date: date, effective: dict[int, tuple[date, date | None]]):
    """Keeps rules whose rate card is in force on the order's date."""
    result = []
    for rule in rules:
        start, end = effective[rule.id]
        if start <= order_date and (end is None or order_date <= end):
            result.append(rule)
    return result


# --- Streams -------------------------------------------------------------------------


def _review(reason: str, **explanation) -> StreamOutcome:
    return StreamOutcome(ReconStatus.NEEDS_REVIEW, explanation={"reason": reason, **explanation})


def evaluate_receivable(
    order: OrderFacts, records: OrderRecords, context: Context
) -> StreamOutcome:
    """What the order should have paid out, against what actually arrived."""
    status = order.delivery_status
    if status in (DeliveryStatus.CREATED, DeliveryStatus.IN_TRANSIT):
        if records.settlements or records.remittances:
            return _review("Money arrived for an order that isn't delivered yet.")
        return StreamOutcome(
            ReconStatus.PENDING,
            explanation={"reason": "Not delivered yet, so no payment is due."},
        )
    if status == DeliveryStatus.CANCELLED:
        received = _sum(records.settlements) + _sum(records.remittances)
        if abs(received) > context.threshold_paise:
            return _review(f"The order is cancelled, but {rupees(received)} was received.")
        return StreamOutcome(
            ReconStatus.SETTLED,
            0,
            received,
            explanation={"reason": "Cancelled, so no payment is expected."},
        )

    event = EVENT_FOR_STATUS[DeliveryStatus(status)]
    rule, problem = find_settlement_rule(order, event, context.settlement_rules)
    if rule is None:
        return _review(problem, event=event)

    payer_id = rule.payer_partner_id if rule.payer_partner_id is not None else order.courier_id
    if payer_id is None:
        return _review("The rule expects the courier to pay, but the order has no courier.")
    payer = context.partners.get(payer_id)
    via_courier = rule.payer_partner_id is None
    lines = records.remittances if via_courier else records.settlements
    other = records.settlements if via_courier else records.remittances

    base = order.value_paise
    if event == "delivered":
        raw = base * 10000 - percent_of(base, rule.pct_bps) - rule.fixed_paise * 10000
    else:
        # A return costs the brand: the partner keeps or charges back this amount.
        raw = -(percent_of(base, rule.pct_bps) + rule.fixed_paise * 10000)
    expected = round_half_up(raw, rule.round_to_paise)

    reported_tcs = _sum(lines, "tcs_paise")
    reported_tds = _sum(lines, "tds_paise")
    net = _sum(lines)
    # Tax a partner withheld still counts as received: it's a credit, not leakage.
    actual = net + reported_tcs + reported_tds
    difference = actual - expected

    due_date = None
    if event == "delivered":
        if order.delivered_at is None:
            return _review("Delivered, but with no delivery date, so the due date is unknown.")
        due_date = order.delivered_at + timedelta(days=payer.settlement_terms_days if payer else 0)

    explanation = {
        "event": event,
        "rule": {
            "id": rule.id,
            "rate_card": rule.rate_card,
            "pct_bps": rule.pct_bps,
            "fixed_paise": rule.fixed_paise,
            "round_to_paise": rule.round_to_paise,
        },
        "payer_partner_id": payer_id,
        "paid_via": "cod_remittance" if via_courier else "settlement",
        "base_paise": base,
        "deduction_paise": round_half_up(percent_of(base, rule.pct_bps), 1) + rule.fixed_paise,
        "expected_paise": expected,
        "net_received_paise": net,
        "tcs_paise": reported_tcs,
        "tds_paise": reported_tds,
        "actual_paise": actual,
        "difference_paise": difference,
        "due_date": due_date.isoformat() if due_date else None,
        "records": [record.as_dict() for record in lines],
        "threshold_paise": context.threshold_paise,
    }

    if other:
        return StreamOutcome(
            ReconStatus.NEEDS_REVIEW,
            expected,
            actual,
            {
                **explanation,
                "reason": "Money arrived through an unexpected route for this payment "
                "method, such as a COD remittance on a prepaid order.",
                "unexpected_records": [record.as_dict() for record in other],
            },
        )
    unexpected_payer = [r for r in lines if r.partner_id != payer_id]
    if unexpected_payer:
        return StreamOutcome(
            ReconStatus.NEEDS_REVIEW,
            expected,
            actual,
            {
                **explanation,
                "reason": "Some payments came from a partner the rate card doesn't "
                "expect to pay this order.",
            },
        )

    if event != "delivered" and not lines and expected < 0:
        # The partner hasn't charged for the return yet. That's not a loss.
        return StreamOutcome(
            ReconStatus.PENDING,
            expected,
            actual,
            {**explanation, "reason": "The return charge hasn't been billed yet."},
        )

    if abs(difference) <= context.threshold_paise:
        if not lines and expected != 0 and due_date and context.as_of <= due_date:
            return StreamOutcome(
                ReconStatus.PENDING,
                expected,
                actual,
                {**explanation, "reason": f"Not due until {due_date:%d %b %Y}."},
            )
        return StreamOutcome(
            ReconStatus.SETTLED,
            expected,
            actual,
            {**explanation, "reason": "Received within the tolerance."},
        )

    if difference > 0:
        return StreamOutcome(
            ReconStatus.NEEDS_REVIEW,
            expected,
            actual,
            {
                **explanation,
                "reason": f"Received {rupees(difference)} more than expected. "
                "An overpayment isn't counted as leakage.",
            },
        )

    not_yet_due = due_date is not None and context.as_of <= due_date
    if not_yet_due:
        what = "Nothing received yet" if not lines else "Partly paid so far"
        return StreamOutcome(
            ReconStatus.PENDING,
            expected,
            actual,
            {**explanation, "reason": f"{what}, but not due until {due_date:%d %b %Y}."},
        )

    tax_missing = (
        order.channel_id in context.tax_withholding_channels
        and lines
        and any(record.tcs_paise is None and record.tds_paise is None for record in lines)
    )
    if tax_missing:
        return StreamOutcome(
            ReconStatus.NEEDS_REVIEW,
            expected,
            actual,
            {
                **explanation,
                "reason": "This partner normally withholds tax, but the file didn't "
                "report it. The shortfall may be tax, so it isn't treated as leakage.",
            },
        )

    category = DiscrepancyCategory.UNPAID_PAST_DUE if not lines else DiscrepancyCategory.SHORT_PAID
    status = ReconStatus.UNPAID_PAST_DUE if not lines else ReconStatus.SHORT_PAID
    reason = (
        (
            f"Nothing received, and it was due on {due_date:%d %b %Y}."
            if not lines
            else f"Received {rupees(actual)} against {rupees(expected)} expected: "
            f"short by {rupees(-difference)}."
        )
        if due_date
        else (f"Charged {rupees(-actual)} against {rupees(-expected)} expected.")
    )
    return StreamOutcome(
        status,
        expected,
        actual,
        {**explanation, "reason": reason},
        DiscrepancyOut(category, payer_id, expected, actual, difference, due_date),
    )


def evaluate_freight(
    order: OrderFacts, records: OrderRecords, context: Context
) -> StreamOutcome | None:
    """Each billed shipment against the rate card. None when nothing was billed."""
    if not records.freight:
        return None
    by_type: dict[tuple[int, str], list[SourceRecord]] = {}
    for line in records.freight:
        by_type.setdefault((line.partner_id, line.extra["shipment_type"]), []).append(line)

    lines_out, reviews = [], []
    overcharged: dict[int, list[tuple[int, int]]] = {}
    expected_total = actual_total = 0
    for (courier_id, shipment_type), lines in by_type.items():
        if len(lines) > 1:
            reviews.append(
                f"{len(lines)} {shipment_type} charges for the same shipment: "
                "possibly billed twice."
            )
        for line in lines:
            # The order's own slab, where known, is what should have been billed.
            slab = order.weight_slab or line.extra.get("weight_slab")
            zone = order.zone or line.extra.get("zone")
            rule, problem = find_freight_rule(
                courier_id, slab, zone, shipment_type, context.freight_rules
            )
            gst = line.extra.get("gst_paise") or 0
            billed = (line.amount_paise or 0) - gst  # GST is compared separately
            entry = {
                **line.as_dict(),
                "billed_excl_gst_paise": billed,
                "slab_used": slab,
                "zone_used": zone,
            }
            if rule is None:
                reviews.append(problem)
                lines_out.append({**entry, "reason": problem})
                continue
            cod = 0
            if shipment_type == ShipmentType.FORWARD and order.payment_method == "cod":
                cod = max(
                    round_half_up(percent_of(order.value_paise, rule.cod_pct_bps), 1),
                    rule.cod_min_paise,
                )
            fuel = round_half_up(percent_of(rule.base_paise, rule.fuel_pct_bps), 1)
            expected = rule.base_paise + cod + fuel
            expected_total += expected
            actual_total += billed
            entry.update(
                {
                    "rule": {
                        "id": rule.id,
                        "rate_card": rule.rate_card,
                        "base_paise": rule.base_paise,
                        "cod_paise": cod,
                        "fuel_paise": fuel,
                        "slab": rule.weight_slab,
                        "zone": rule.zone,
                    },
                    "expected_paise": expected,
                    "difference_paise": expected - billed,
                }
            )
            billed_slab = line.extra.get("weight_slab")
            if billed_slab and slab and billed_slab.casefold() != slab.casefold():
                entry["note"] = f"Billed as slab {billed_slab}, but the order is {slab}."
            if billed - expected > context.threshold_paise:
                overcharged.setdefault(courier_id, []).append((expected, billed))
            lines_out.append(entry)

    explanation = {
        "lines": lines_out,
        "expected_paise": -expected_total,
        "actual_paise": -actual_total,
        "threshold_paise": context.threshold_paise,
    }
    if overcharged:
        courier_id, pairs = max(
            overcharged.items(), key=lambda item: sum(b - e for e, b in item[1])
        )
        expected = -sum(e for e, _ in pairs)
        actual = -sum(b for _, b in pairs)
        notes = [line["note"] for line in lines_out if line.get("note")]
        reason = f"Charged {rupees(-actual)} against {rupees(-expected)} expected by the rate card."
        if notes:
            reason += " " + notes[0]
        return StreamOutcome(
            ReconStatus.FREIGHT_OVERCHARGED,
            expected,
            actual,
            {**explanation, "reason": reason, "also_review": reviews},
            DiscrepancyOut(
                DiscrepancyCategory.FREIGHT_OVERCHARGED,
                courier_id,
                expected,
                actual,
                actual - expected,
                None,
            ),
        )
    if reviews:
        return StreamOutcome(
            ReconStatus.NEEDS_REVIEW,
            -expected_total,
            -actual_total,
            {**explanation, "reason": reviews[0], "also_review": reviews[1:]},
        )
    return StreamOutcome(
        ReconStatus.SETTLED,
        -expected_total,
        -actual_total,
        {**explanation, "reason": "Every charge matches the rate card."},
    )


def evaluate_returns(
    order: OrderFacts, records: OrderRecords, context: Context
) -> StreamOutcome | None:
    """A return only counts as received when the warehouse has scanned it in."""
    returning = order.delivery_status in RETURN_STATUSES or records.returns
    if not returning:
        return None
    returned_at = min(
        [record.record_date for record in records.returns if record.record_date]
        + ([order.returned_at] if order.returned_at else []),
        default=None,
    )
    return_partner = records.returns[0].partner_id if records.returns else order.courier_id
    explanation = {
        "returned_at": returned_at.isoformat() if returned_at else None,
        "return_records": [record.as_dict() for record in records.returns],
        "receipts": [record.as_dict() for record in records.receipts],
        "value_at_risk_paise": order.value_paise,
    }
    if records.receipts:
        expected_qty = max(
            [record.extra.get("quantity") or 0 for record in records.returns]
            + [order.quantity or 0]
        )
        received_qty = sum(record.extra.get("quantity") or 0 for record in records.receipts)
        damaged = [
            r
            for r in records.receipts
            if any(word in (r.extra.get("condition") or "").casefold() for word in DAMAGED)
        ]
        if damaged:
            return _review(
                "The warehouse recorded the return as damaged or incomplete.", **explanation
            )
        if expected_qty and received_qty and received_qty < expected_qty:
            return _review(
                f"The warehouse received {received_qty} of {expected_qty} units.", **explanation
            )
        return StreamOutcome(
            ReconStatus.SETTLED,
            explanation={**explanation, "reason": "The warehouse scanned the return in."},
        )

    if returned_at is None:
        return _review(
            "Marked as returned, but with no return date, so the grace period can't be applied.",
            **explanation,
        )
    if return_partner is None:
        return _review(
            "Marked as returned, but no courier or partner is responsible for it.", **explanation
        )
    grace = (
        context.partners[return_partner].return_grace_days
        if return_partner in context.partners
        else 0
    )
    due = returned_at + timedelta(days=grace)
    explanation.update(
        {
            "grace_days": grace,
            "due_date": due.isoformat(),
            "days_since_return": (context.as_of - returned_at).days,
        }
    )
    if context.as_of <= due:
        return StreamOutcome(
            ReconStatus.PENDING,
            explanation={
                **explanation,
                "reason": f"Returned, and the warehouse has until {due:%d %b %Y} to scan it in.",
            },
        )
    value = order.value_paise
    return StreamOutcome(
        ReconStatus.RETURN_NOT_RECEIVED,
        value,
        0,
        {
            **explanation,
            "reason": f"Returned on {returned_at:%d %b %Y}, but not scanned into the "
            f"warehouse within {grace} days. {rupees(value)} of stock is unaccounted for.",
        },
        DiscrepancyOut(
            DiscrepancyCategory.RETURN_NOT_RECEIVED, return_partner, value, 0, -value, due
        ),
    )


def evaluate(order: OrderFacts, records: OrderRecords, context: Context) -> Evaluation:
    streams = {
        "receivable": evaluate_receivable(order, records, context),
        "freight": evaluate_freight(order, records, context),
        "returns": evaluate_returns(order, records, context),
    }
    present = {name: outcome for name, outcome in streams.items() if outcome is not None}
    primary_name, primary = min(present.items(), key=lambda item: PRECEDENCE.index(item[1].status))
    tcs = streams["receivable"].explanation.get("tcs_paise")
    tds = streams["receivable"].explanation.get("tds_paise")
    discrepancies = [o.discrepancy for o in present.values() if o.discrepancy is not None]
    explanation = {
        "primary_stream": primary_name,
        "reason": primary.explanation.get("reason"),
        "precedence": [status.value for status in PRECEDENCE],
        "streams": {
            name: {"status": outcome.status.value, **outcome.explanation}
            for name, outcome in present.items()
        },
    }
    return Evaluation(
        primary_status=primary.status,
        expected_paise=primary.expected_paise,
        actual_paise=primary.actual_paise,
        tcs_paise=tcs,
        tds_paise=tds,
        discrepancies=discrepancies,
        explanation=explanation,
    )
