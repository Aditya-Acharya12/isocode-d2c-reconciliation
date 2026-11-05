"""Checks the guarantees that the database itself must provide."""

from datetime import date

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import insert, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.database import get_engine
from app.models import (
    AuditLog,
    BackgroundJob,
    Base,
    Discrepancy,
    FreightRateRule,
    Order,
    Partner,
    RateCard,
    ReconciliationResult,
    ReconciliationRun,
    SalesChannel,
    SettlementLine,
    UploadedFile,
)
from app.models.enums import JobKind


def test_migrations_produce_exactly_the_declared_models():
    with get_engine().connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_every_foreign_key_on_a_lookup_path_is_indexed():
    """Transaction-to-order lookups are the core of reconciliation."""
    inspector = inspect(get_engine())
    for table in ("settlement_lines", "cod_remittances", "freight_invoice_lines",
                  "return_records", "warehouse_receipts"):
        indexed = {tuple(i["column_names"]) for i in inspector.get_indexes(table)}
        assert ("order_id",) in indexed, table
        assert ("tracking_id",) in indexed, table
        assert ("external_order_ref",) in indexed, table


@pytest.fixture
def base_rows(db, users):
    partner = Partner(name="M1", type="marketplace", settlement_terms_days=7, return_grace_days=7)
    channel = SalesChannel(code="M1", name="Marketplace 1")
    db.add_all([partner, channel])
    db.flush()
    file = UploadedFile(
        partner_id=partner.id,
        file_type="order",
        filename="orders.csv",
        sha256="a" * 64,
        size_bytes=1,
        uploaded_by=users["finance"].id,
    )
    db.add(file)
    db.flush()
    db.commit()
    return {"partner": partner, "channel": channel, "file": file, "user": users["finance"]}


def _order(rows, external_id="ORD-1", channel_id=None) -> Order:
    return Order(
        channel_id=channel_id or rows["channel"].id,
        external_order_id=external_id,
        order_date=date(2026, 10, 1),
        order_value=1199,
        payment_method="prepaid",
        delivery_status="delivered",
        source_file_id=rows["file"].id,
        source_row_number=2,
    )


def _settlement(rows, dedupe_key="ref-1") -> SettlementLine:
    return SettlementLine(
        partner_id=rows["partner"].id,
        source_file_id=rows["file"].id,
        source_row_number=2,
        dedupe_key=dedupe_key,
        settlement_ref=dedupe_key,
        settlement_date=date(2026, 10, 10),
        net_amount=862,
    )


def test_order_ids_are_unique_within_a_channel_only(db, base_rows):
    other = SalesChannel(code="M2", name="Marketplace 2")
    db.add(other)
    db.flush()
    db.add_all([_order(base_rows), _order(base_rows, channel_id=other.id)])
    db.commit()  # the same ID on two channels is fine

    db.add(_order(base_rows))
    with pytest.raises(IntegrityError):
        db.commit()


def test_a_settlement_line_cannot_load_twice(db, base_rows):
    db.add(_settlement(base_rows))
    db.commit()
    db.add(_settlement(base_rows))
    with pytest.raises(IntegrityError):
        db.commit()


def test_money_keeps_exact_paise(db, base_rows):
    line = _settlement(base_rows)
    line.net_amount = "855.09"
    db.add(line)
    db.commit()
    db.expire(line)
    assert str(line.net_amount) == "855.09"


def test_the_same_file_cannot_be_recorded_twice(db, base_rows):
    rows = base_rows
    db.add(
        UploadedFile(
            partner_id=rows["partner"].id,
            file_type="order",
            filename="renamed.csv",
            sha256=rows["file"].sha256,
            size_bytes=1,
            uploaded_by=rows["user"].id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def _run(db, rows, key) -> ReconciliationRun:
    job = BackgroundJob(
        kind=JobKind.RECONCILIATION,
        payload={},
        idempotency_key=key,
        created_by=rows["user"].id,
    )
    db.add(job)
    db.flush()
    run = ReconciliationRun(
        as_of_date=date(2026, 11, 1),
        scope={},
        background_job_id=job.id,
        triggered_by=rows["user"].id,
        rate_card_ids=[],
        settings_snapshot={},
    )
    db.add(run)
    db.flush()
    return run


def test_an_order_has_only_one_current_result(db, base_rows):
    order = _order(base_rows)
    db.add(order)
    db.flush()
    first, second = _run(db, base_rows, "a"), _run(db, base_rows, "b")
    db.add(ReconciliationResult(run_id=first.id, order_id=order.id, primary_status="settled"))
    db.commit()

    db.add(ReconciliationResult(run_id=second.id, order_id=order.id, primary_status="settled"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        ReconciliationResult(
            run_id=second.id, order_id=order.id, primary_status="settled", is_current=False
        )
    )
    db.commit()  # history alongside a current row is fine


def test_a_rerun_cannot_duplicate_a_discrepancy(db, base_rows):
    order = _order(base_rows)
    db.add(order)
    db.flush()
    run = _run(db, base_rows, "a")

    def discrepancy() -> Discrepancy:
        return Discrepancy(
            order_id=order.id,
            category="short_paid",
            counterparty_partner_id=base_rows["partner"].id,
            first_seen_run_id=run.id,
            last_seen_run_id=run.id,
        )

    db.add(discrepancy())
    db.commit()
    db.add(discrepancy())
    with pytest.raises(IntegrityError):
        db.commit()


def test_a_background_job_cannot_be_queued_twice(db, base_rows):
    def job() -> BackgroundJob:
        return BackgroundJob(
            kind=JobKind.IMPORT,
            payload={},
            idempotency_key="import:file-1:mapping-1",
            created_by=base_rows["user"].id,
        )

    db.add(job())
    db.commit()
    db.add(job())
    with pytest.raises(IntegrityError):
        db.commit()


def test_published_rate_cards_cannot_overlap(db):
    def card(version, start, end, status="published") -> RateCard:
        return RateCard(
            name="Default",
            version=version,
            effective_from=start,
            effective_to=end,
            status=status,
        )

    db.add(card(1, date(2026, 1, 1), date(2026, 6, 30)))
    db.commit()
    db.add(card(2, date(2026, 7, 1), None))  # starts after the first ends
    db.add(card(3, date(2026, 3, 1), None, status="draft"))  # drafts may overlap
    db.commit()

    db.add(card(4, date(2026, 6, 1), date(2026, 8, 1)))
    with pytest.raises(IntegrityError):
        db.commit()


def test_freight_rules_treat_an_empty_zone_as_a_duplicate(db, base_rows):
    rate_card = RateCard(name="F", version=1, effective_from=date(2026, 1, 1))
    db.add(rate_card)
    db.flush()

    def rule() -> FreightRateRule:
        return FreightRateRule(
            rate_card_id=rate_card.id,
            courier_id=base_rows["partner"].id,
            weight_slab="500g",
            zone=None,
            shipment_type="rto",
            base_paise=9600,
        )

    db.add(rule())
    db.commit()
    db.add(rule())
    with pytest.raises(IntegrityError):
        db.commit()


def test_audit_log_rows_cannot_be_changed_or_deleted(db):
    db.execute(insert(AuditLog).values(action="test", entity_type="test"))
    db.commit()

    for statement in ("UPDATE audit_logs SET action = 'tampered'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()
