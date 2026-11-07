"""Runs reconciliation against the database: link, gather, evaluate, persist.

All of it happens in one transaction, under an advisory lock, so two runs can never
interleave, and a failed run leaves nothing behind. Re-running gives the same answer
for the same data, and never duplicates a discrepancy.
"""

import json
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    BackgroundJob,
    FreightRateRule,
    Partner,
    RateCard,
    ReconciliationRun,
    Setting,
    SettlementRateRule,
    TaxRule,
)
from app.models.enums import JobStatus, RateCardStatus
from app.services.audit import record_audit
from app.services.reconciliation import engine

RUN_LOCK_KEY = 738201947
DEFAULT_THRESHOLD_PAISE = 1000
# Orders evaluated at a time, so memory stays flat however many orders there are.
CHUNK_SIZE = 5000

TRANSACTION_TABLES = {
    "settlement": "settlement_lines",
    "cod_remittance": "cod_remittances",
    "freight": "freight_invoice_lines",
    "return": "return_records",
    "warehouse_receipt": "warehouse_receipts",
}

# Partners attached to a sales channel (marketplaces, quick commerce) can only settle that
# channel's orders. Others (couriers, gateways, warehouses) may handle any channel.
_SCOPE = """
    (NOT EXISTS (SELECT 1 FROM sales_channels sc WHERE sc.partner_id = t.partner_id)
     OR o.channel_id IN (SELECT sc.id FROM sales_channels sc WHERE sc.partner_id = t.partner_id))
"""
_LINK_PASSES = (
    ("exact_order_id", "o.external_order_id = t.external_order_ref"),
    (
        "normalised_order_id",
        "upper(regexp_replace(o.external_order_id, '[^A-Za-z0-9]', '', 'g')) = "
        "upper(regexp_replace(t.external_order_ref, '[^A-Za-z0-9]', '', 'g'))",
    ),
    ("tracking_id", "o.tracking_id = t.tracking_id"),
)


def link_records(db: Session) -> dict[str, int]:
    """Links unlinked records to orders. Only fills empty links, and never guesses.

    A record that matches more than one order stays unlinked, for a person to review.
    """
    linked: Counter = Counter()
    for kind, table in TRANSACTION_TABLES.items():
        for method, condition in _LINK_PASSES:
            column = "t.tracking_id" if method == "tracking_id" else "t.external_order_ref"
            result = db.execute(
                text(f"""
                UPDATE {table} AS target SET order_id = m.order_id, match_method = '{method}'
                FROM (
                    SELECT t.id, min(o.id) AS order_id
                    FROM {table} t JOIN orders o ON {condition}
                    WHERE t.order_id IS NULL AND {column} IS NOT NULL AND {_SCOPE}
                    GROUP BY t.id HAVING count(*) = 1
                ) m
                WHERE target.id = m.id
            """)
            )  # noqa: S608 - fixed table names and conditions
            linked[f"{kind}:{method}"] += result.rowcount
    return dict(linked)


def unmatched_counts(db: Session) -> dict[str, int]:
    return {
        kind: db.scalar(text(f"SELECT count(*) FROM {table} WHERE order_id IS NULL"))  # noqa: S608
        for kind, table in TRANSACTION_TABLES.items()
    }


def _paise(column: str) -> str:
    return f"CASE WHEN {column} IS NULL THEN NULL ELSE round({column} * 100)::bigint END"


def _load_records(db: Session, first: int, last: int) -> dict[int, engine.OrderRecords]:
    """The linked records of orders with IDs from first to last."""
    span = {"first": first, "last": last}
    records: dict[int, engine.OrderRecords] = defaultdict(engine.OrderRecords)
    rows = db.execute(
        text(f"""
        SELECT order_id, id, partner_id, source_file_id, source_row_number, settlement_ref,
               settlement_date, {_paise("net_amount")} AS amount, {_paise("tcs_amount")} AS tcs,
               {_paise("tds_amount")} AS tds, transaction_type, match_method
        FROM settlement_lines WHERE order_id BETWEEN :first AND :last ORDER BY id
    """),
        span,
    )
    for r in rows:
        records[r.order_id].settlements.append(
            engine.SourceRecord(
                "settlement",
                r.id,
                r.partner_id,
                r.source_file_id,
                r.source_row_number,
                r.settlement_ref,
                r.settlement_date,
                r.amount,
                r.tcs,
                r.tds,
                {"transaction_type": r.transaction_type, "match_method": r.match_method},
            )
        )
    rows = db.execute(
        text(f"""
        SELECT order_id, id, partner_id, source_file_id, source_row_number, remittance_ref,
               remittance_date, {_paise("net_remittance")} AS amount,
               {_paise("cod_amount")} AS cod_amount, match_method
        FROM cod_remittances WHERE order_id BETWEEN :first AND :last ORDER BY id
    """),
        span,
    )
    for r in rows:
        records[r.order_id].remittances.append(
            engine.SourceRecord(
                "cod_remittance",
                r.id,
                r.partner_id,
                r.source_file_id,
                r.source_row_number,
                r.remittance_ref,
                r.remittance_date,
                r.amount,
                extra={"cod_collected_paise": r.cod_amount, "match_method": r.match_method},
            )
        )
    rows = db.execute(
        text(f"""
        SELECT order_id, id, partner_id, source_file_id, source_row_number, invoice_no,
               invoice_date, {_paise("total_charge")} AS amount,
               {_paise("gst_on_freight")} AS gst, shipment_type, weight_slab, zone,
               billed_weight_grams, tracking_id, match_method
        FROM freight_invoice_lines WHERE order_id BETWEEN :first AND :last ORDER BY id
    """),
        span,
    )
    for r in rows:
        records[r.order_id].freight.append(
            engine.SourceRecord(
                "freight",
                r.id,
                r.partner_id,
                r.source_file_id,
                r.source_row_number,
                r.invoice_no,
                r.invoice_date,
                r.amount,
                extra={
                    "gst_paise": r.gst,
                    "shipment_type": r.shipment_type,
                    "weight_slab": r.weight_slab,
                    "zone": r.zone,
                    "billed_weight_grams": r.billed_weight_grams,
                    "tracking_id": r.tracking_id,
                    "match_method": r.match_method,
                },
            )
        )
    rows = db.execute(
        text("""
        SELECT order_id, id, partner_id, source_file_id, source_row_number, return_type,
               returned_at, return_status, quantity, tracking_id, match_method
        FROM return_records WHERE order_id BETWEEN :first AND :last ORDER BY id
    """),
        span,
    )
    for r in rows:
        records[r.order_id].returns.append(
            engine.SourceRecord(
                "return",
                r.id,
                r.partner_id,
                r.source_file_id,
                r.source_row_number,
                r.tracking_id,
                r.returned_at,
                extra={
                    "return_type": r.return_type,
                    "return_status": r.return_status,
                    "quantity": r.quantity,
                    "match_method": r.match_method,
                },
            )
        )
    rows = db.execute(
        text("""
        SELECT order_id, id, partner_id, source_file_id, source_row_number, receipt_ref,
               received_at, quantity_received, condition, match_method
        FROM warehouse_receipts WHERE order_id BETWEEN :first AND :last ORDER BY id
    """),
        span,
    )
    for r in rows:
        records[r.order_id].receipts.append(
            engine.SourceRecord(
                "warehouse_receipt",
                r.id,
                r.partner_id,
                r.source_file_id,
                r.source_row_number,
                r.receipt_ref,
                r.received_at,
                extra={
                    "quantity": r.quantity_received,
                    "condition": r.condition,
                    "match_method": r.match_method,
                },
            )
        )
    return records


def _load_orders(db: Session, after: int, limit: int) -> list[engine.OrderFacts]:
    """The next orders by ID, after the given one."""
    rows = db.execute(
        text(f"""
        SELECT id, external_order_id, channel_id, order_date, {_paise("order_value")} AS value,
               payment_method, delivery_status, delivered_at, returned_at, courier_id,
               weight_slab, zone, quantity, source_file_id, source_row_number
        FROM orders WHERE id > :after ORDER BY id LIMIT :limit
    """),
        {"after": after, "limit": limit},
    )
    return [
        engine.OrderFacts(
            r.id,
            r.external_order_id,
            r.channel_id,
            r.order_date,
            r.value,
            r.payment_method,
            r.delivery_status,
            r.delivered_at,
            r.returned_at,
            r.courier_id,
            r.weight_slab,
            r.zone,
            r.quantity,
            r.source_file_id,
            r.source_row_number,
        )
        for r in rows
    ]


class RuleBook:
    """Published rate cards, so that each order uses the rules in force on its date."""

    def __init__(self, db: Session) -> None:
        cards = {
            card.id: card
            for card in db.scalars(
                select(RateCard).where(RateCard.status == RateCardStatus.PUBLISHED)
            )
        }
        self.card_ids = sorted(cards)
        self.settlement: list[tuple[RateCard, engine.SettlementRule]] = []
        self.freight: list[tuple[RateCard, engine.FreightRule]] = []
        label = lambda card: f"{card.name} v{card.version}"  # noqa: E731
        for rule in db.scalars(
            select(SettlementRateRule).where(SettlementRateRule.rate_card_id.in_(self.card_ids))
        ):
            card = cards[rule.rate_card_id]
            self.settlement.append(
                (
                    card,
                    engine.SettlementRule(
                        rule.id,
                        label(card),
                        rule.channel_id,
                        rule.payment_method.value,
                        rule.event.value,
                        rule.payer_partner_id,
                        rule.pct_bps,
                        rule.fixed_paise,
                        rule.round_to_paise,
                    ),
                )
            )
        for rule in db.scalars(
            select(FreightRateRule).where(FreightRateRule.rate_card_id.in_(self.card_ids))
        ):
            card = cards[rule.rate_card_id]
            self.freight.append(
                (
                    card,
                    engine.FreightRule(
                        rule.id,
                        label(card),
                        rule.courier_id,
                        rule.weight_slab,
                        rule.zone,
                        rule.shipment_type.value,
                        rule.base_paise,
                        rule.cod_pct_bps,
                        rule.cod_min_paise,
                        rule.fuel_pct_bps,
                    ),
                )
            )
        self._cache: dict[date, tuple[list, list]] = {}

    @staticmethod
    def _in_force(card: RateCard, on: date) -> bool:
        return card.effective_from <= on and (card.effective_to is None or on <= card.effective_to)

    def for_date(self, on: date) -> tuple[list[engine.SettlementRule], list[engine.FreightRule]]:
        if on not in self._cache:
            self._cache[on] = (
                [rule for card, rule in self.settlement if self._in_force(card, on)],
                [rule for card, rule in self.freight if self._in_force(card, on)],
            )
        return self._cache[on]


def threshold_paise(db: Session) -> int:
    setting = db.get(Setting, "discrepancy_threshold_paise")
    return int(setting.value) if setting is not None else DEFAULT_THRESHOLD_PAISE


def _rupees(paise: int) -> Decimal:
    """Exact, for a NUMERIC column. Never a float."""
    return Decimal(paise).scaleb(-2)


def _copy_rows(db: Session, statement: str, rows: list[tuple]) -> None:
    connection = db.connection().connection.dbapi_connection
    with connection.cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)


def reconcile(db: Session, run: ReconciliationRun, actor_id: int) -> dict:
    """Does the whole run inside the caller's transaction. Returns the run's counts."""
    timings: dict[str, int] = {}
    started = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal started
        now = time.perf_counter()
        timings[name] = timings.get(name, 0) + int((now - started) * 1000)
        started = now

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RUN_LOCK_KEY})
    linked = link_records(db)
    lap("link")

    partners = {
        p.id: engine.Partner(p.id, p.name, p.settlement_terms_days, p.return_grace_days)
        for p in db.scalars(select(Partner))
    }
    rules = RuleBook(db)
    tax_channels = set(db.scalars(select(TaxRule.channel_id).distinct()))
    threshold = threshold_paise(db)
    # Full result rows are kept for the current run only: at 100,000 orders each run takes
    # about 175 MB. Readers keep seeing the previous results until this transaction commits.
    # Run summaries, discrepancies, claim evidence and the audit log keep the history.
    db.execute(text("DELETE FROM reconciliation_results"))
    lap("load")

    statuses: Counter = Counter()
    rows: list[tuple] = []
    order_count, after = 0, 0
    while orders := _load_orders(db, after, CHUNK_SIZE):
        records = _load_records(db, orders[0].id, orders[-1].id)
        lap("load")

        results = []
        for order in orders:
            settlement_rules, freight_rules = rules.for_date(order.order_date)
            context = engine.Context(
                run.as_of_date, threshold, partners, settlement_rules, freight_rules, tax_channels
            )
            evaluation = engine.evaluate(
                order, records.get(order.id, engine.OrderRecords()), context
            )
            statuses[evaluation.primary_status.value] += 1
            results.append(
                (
                    run.id,
                    order.id,
                    evaluation.primary_status.value,
                    evaluation.expected_paise,
                    evaluation.actual_paise,
                    evaluation.tcs_paise,
                    evaluation.tds_paise,
                    json.dumps(evaluation.explanation, default=str),
                    True,
                )
            )
            rows.extend(
                (
                    order.id,
                    d.category.value,
                    d.counterparty_partner_id,
                    _rupees(d.expected_paise),
                    _rupees(d.actual_paise),
                    _rupees(d.difference_paise),
                    d.due_date,
                )
                for d in evaluation.discrepancies
            )
        lap("evaluate")

        _copy_rows(
            db,
            "COPY reconciliation_results (run_id, order_id, primary_status, expected_paise, "
            "actual_paise, tcs_paise, tds_paise, explanation, is_current) FROM STDIN",
            results,
        )
        lap("save_results")
        order_count += len(orders)
        after = orders[-1].id

    if rows:
        # COPY into a scratch table, then one upsert: far faster than a statement per row.
        db.execute(
            text("""
            CREATE TEMP TABLE run_discrepancies (
                order_id bigint, category text, partner_id bigint, expected numeric(14, 2),
                actual numeric(14, 2), difference numeric(14, 2), due_date date
            ) ON COMMIT DROP
        """)
        )
        _copy_rows(db, "COPY run_discrepancies FROM STDIN", rows)
        db.execute(
            text("""
            INSERT INTO discrepancies (order_id, category, counterparty_partner_id, expected,
                actual, difference, due_date, state, first_seen_run_id, last_seen_run_id)
            SELECT order_id, category, partner_id, expected, actual,
                difference, due_date, 'open', :run, :run
            FROM run_discrepancies
            ON CONFLICT (order_id, category, counterparty_partner_id) DO UPDATE SET
                expected = EXCLUDED.expected, actual = EXCLUDED.actual,
                difference = EXCLUDED.difference, due_date = EXCLUDED.due_date,
                state = 'open', last_seen_run_id = EXCLUDED.last_seen_run_id, updated_at = now()
        """),
            {"run": run.id},
        )
    resolved = db.execute(
        text("""
        UPDATE discrepancies SET state = 'resolved', updated_at = now()
        WHERE state = 'open' AND last_seen_run_id <> :run
    """),
        {"run": run.id},
    ).rowcount
    lap("save_discrepancies")

    counts = {
        "orders": order_count,
        "statuses": dict(statuses),
        "discrepancies_open": len(rows),
        "discrepancies_resolved": resolved,
        "linked": linked,
        "unmatched": unmatched_counts(db),
    }
    run.counts = counts
    run.timings_ms = timings
    run.rate_card_ids = rules.card_ids
    run.settings_snapshot = {"threshold_paise": threshold}
    run.status = JobStatus.SUCCEEDED
    record_audit(
        db,
        actor_id=actor_id,
        action="reconciliation.run",
        entity_type="reconciliation_run",
        entity_id=run.id,
        after={"as_of": run.as_of_date.isoformat(), **counts},
    )
    return counts


def mark_failed(db: Session, job: BackgroundJob) -> None:
    run = db.scalar(select(ReconciliationRun).where(ReconciliationRun.background_job_id == job.id))
    if run is not None:
        run.status = JobStatus.FAILED
