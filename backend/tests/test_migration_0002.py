"""Rehearses migration 0002 on a populated database, as the real one will be."""

import json

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import get_direct_engine
from tests.conftest import alembic_config, reset_schema

TABLES = (
    "users", "partners", "sales_channels", "settings", "audit_logs", "uploaded_files",
    "uploaded_file_blobs", "column_mappings", "mapping_versions", "background_jobs",
    "import_jobs", "import_row_errors", "settlement_lines", "orders",
)


@pytest.fixture
def at_0001():
    """A populated database on the old schema. Leaves the latest schema for other tests."""
    reset_schema()
    command.upgrade(alembic_config(), "0001")
    with get_direct_engine().begin() as c:
        c.execute(text("""
            INSERT INTO users (id, email, full_name, password_hash, role, is_active, token_version)
            VALUES (1, 'a@b.example', 'A', 'hash', 'finance_executive', true, 3);
            INSERT INTO partners (id, name, type, settlement_terms_days, return_grace_days, is_active)
            VALUES (1, 'Marketplace 1', 'marketplace', 15, 15, true),
                   (2, 'Courier A', 'courier', 10, 15, true);
            INSERT INTO sales_channels (id, code, name, partner_id) VALUES (1, 'WEB', 'Website', NULL);
            INSERT INTO settings (key, value) VALUES ('threshold', '1000');
            INSERT INTO audit_logs (actor_id, action, entity_type, after)
            VALUES (1, 'auth.login', 'user', '{"a": 1}');
            INSERT INTO uploaded_files
                (id, partner_id, file_type, filename, sha256, size_bytes, uploaded_by, is_synthetic)
            VALUES (1, 1, 'settlement', 'a.csv', repeat('a', 64), 10, 1, true);
            INSERT INTO uploaded_file_blobs (file_id, content_gzip) VALUES (1, '\\x00ff');
            INSERT INTO column_mappings (id, partner_id, file_type) VALUES (1, 1, 'settlement');
            INSERT INTO mapping_versions
                (id, mapping_id, version, spec, header_fingerprint, source, approved_by, approved_at)
            VALUES (1, 1, 1, '{"fields": {}}', repeat('b', 64), 'manual', 1, now());
            UPDATE column_mappings SET active_version_id = 1;
            INSERT INTO background_jobs
                (id, kind, status, payload, idempotency_key, attempts, max_attempts, created_by)
            VALUES (1, 'import', 'succeeded', '{}', 'import:1', 1, 3, 1);
            INSERT INTO import_jobs (id, file_id, mapping_version_id, background_job_id, rows_total)
            VALUES (1, 1, 1, 1, 5);
            INSERT INTO import_row_errors (import_job_id, row_number, field, code, raw_value)
            VALUES (1, 4, 'net_amount', 'invalid_amount', 'abc');
            INSERT INTO settlement_lines (partner_id, source_file_id, source_row_number, dedupe_key,
                                          settlement_ref, settlement_date, net_amount)
            VALUES (1, 1, 2, 'k1', 'SET-1', '2026-10-01', 855.09);
        """))
    yield
    reset_schema()
    command.upgrade(alembic_config(), "head")


def _snapshot() -> dict[str, list[str]]:
    """Every row of every table, with each row's old columns as JSON."""
    result = {}
    with get_direct_engine().connect() as c:
        for table in TABLES:
            rows = c.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY 1::text")).scalars()  # noqa: S608
            result[table] = [json.dumps(row, sort_keys=True) for row in rows]
    return result


NEW_COLUMNS = {"result", "channel_id", "detection", "rows_excluded", "rows_warned", "summary",
               "severity", "message"}


def _without_new_columns(snapshot: dict[str, list[str]]) -> dict[str, list]:
    return {
        table: sorted(
            json.dumps(
                {k: v for k, v in json.loads(row).items() if k not in NEW_COLUMNS}, sort_keys=True
            )
            for row in rows
        )
        for table, rows in snapshot.items()
    }


def _version() -> list[str]:
    with get_direct_engine().connect() as c:
        return list(c.execute(text("SELECT version_num FROM alembic_version")).scalars())


def test_upgrade_changes_no_existing_row(at_0001):
    before = _without_new_columns(_snapshot())
    assert all(before[table] for table in TABLES if table != "orders")

    command.upgrade(alembic_config(), "0002")

    assert _version() == ["0002"]
    assert _without_new_columns(_snapshot()) == before  # the old columns of every row, unchanged


def test_upgrade_gives_existing_rows_sensible_new_values(at_0001):
    command.upgrade(alembic_config(), "0002")
    with get_direct_engine().connect() as c:
        file = c.execute(text("SELECT partner_id, channel_id, detection FROM uploaded_files")).one()
        assert (file.partner_id, file.channel_id, file.detection) == (1, None, None)
        issue = c.execute(text("SELECT severity, message FROM import_row_errors")).one()
        assert (issue.severity, issue.message) == ("error", None)
        counts = c.execute(text("SELECT rows_total, rows_excluded, summary FROM import_jobs")).one()
        assert (counts.rows_total, counts.rows_excluded, counts.summary) == (5, None, None)


def test_upgrade_only_widens_what_can_be_stored(at_0001):
    command.upgrade(alembic_config(), "0002")
    with get_direct_engine().begin() as c:
        c.execute(text(
            "INSERT INTO partners (id, name, type, settlement_terms_days, return_grace_days,"
            " is_active) VALUES (3, 'Warehouse', 'warehouse', 0, 0, true)"
        ))
        c.execute(text(
            "INSERT INTO uploaded_files (id, channel_id, file_type, filename, sha256, size_bytes,"
            " uploaded_by, is_synthetic) VALUES (2, 1, 'order', 'o.csv', repeat('c', 64), 1, 1, true)"
        ))


def test_upgrade_keeps_refusing_bad_data(at_0001):
    command.upgrade(alembic_config(), "0002")
    duplicate = (
        "INSERT INTO uploaded_files (partner_id, file_type, filename, sha256, size_bytes,"
        " uploaded_by, is_synthetic) VALUES (1, 'settlement', 'b.csv', repeat('a', 64), 1, 1, true)"
    )
    no_source = (
        "INSERT INTO uploaded_files (file_type, filename, sha256, size_bytes, uploaded_by,"
        " is_synthetic) VALUES ('order', 'c.csv', repeat('d', 64), 1, 1, true)"
    )
    both = (
        "INSERT INTO uploaded_files (partner_id, channel_id, file_type, filename, sha256,"
        " size_bytes, uploaded_by, is_synthetic)"
        " VALUES (1, 1, 'order', 'd.csv', repeat('e', 64), 1, 1, true)"
    )
    for statement in (duplicate, no_source, both):
        with pytest.raises(IntegrityError), get_direct_engine().begin() as c:
            c.execute(text(statement))


def test_a_failed_upgrade_rolls_back_completely(at_0001):
    """0001 allowed two imports of a file. 0002 doesn't, so it must fail with nothing changed."""
    with get_direct_engine().begin() as c:
        c.execute(text(
            "INSERT INTO background_jobs (id, kind, status, payload, idempotency_key, attempts,"
            " max_attempts, created_by) VALUES (2, 'import', 'failed', '{}', 'import:again', 1, 3, 1)"
        ))
        c.execute(text(
            "INSERT INTO import_jobs (id, file_id, mapping_version_id, background_job_id)"
            " VALUES (2, 1, 1, 2)"
        ))
    before = _snapshot()

    with pytest.raises(IntegrityError):
        command.upgrade(alembic_config(), "0002")

    assert _version() == ["0001"]
    assert _snapshot() == before
    columns = {column["name"] for column in inspect(get_direct_engine()).get_columns("uploaded_files")}
    assert "channel_id" not in columns


def test_downgrade_without_new_data_restores_the_old_rows(at_0001):
    before = _without_new_columns(_snapshot())
    command.upgrade(alembic_config(), "0002")
    command.downgrade(alembic_config(), "0001")
    assert _version() == ["0001"]
    assert _without_new_columns(_snapshot()) == before


def test_downgrade_deletes_data_that_only_the_new_schema_can_hold(at_0001):
    """This is why downgrade isn't a rollback plan. Nothing runs it automatically."""
    command.upgrade(alembic_config(), "0002")
    with get_direct_engine().begin() as c:
        c.execute(text(
            "INSERT INTO uploaded_files (id, channel_id, file_type, filename, sha256, size_bytes,"
            " uploaded_by, is_synthetic) VALUES (2, 1, 'order', 'o.csv', repeat('c', 64), 1, 1, true)"
        ))
        c.execute(text(
            "INSERT INTO orders (channel_id, external_order_id, order_date, order_value,"
            " payment_method, delivery_status, source_file_id, source_row_number)"
            " VALUES (1, 'O1', '2026-10-01', 100, 'cod', 'delivered', 2, 2)"
        ))
    command.downgrade(alembic_config(), "0001")
    with get_direct_engine().connect() as c:
        assert c.scalar(text("SELECT count(*) FROM orders")) == 0  # imported orders are lost
        assert c.scalar(text("SELECT count(*) FROM uploaded_files")) == 1


def test_downgrade_refuses_and_rolls_back_when_records_depend_on_a_warehouse(at_0001):
    command.upgrade(alembic_config(), "0002")
    with get_direct_engine().begin() as c:
        c.execute(text(
            "INSERT INTO partners (id, name, type, settlement_terms_days, return_grace_days,"
            " is_active) VALUES (3, 'Warehouse', 'warehouse', 0, 0, true)"
        ))
        c.execute(text(
            "INSERT INTO uploaded_files (id, partner_id, file_type, filename, sha256, size_bytes,"
            " uploaded_by, is_synthetic)"
            " VALUES (3, 3, 'warehouse_receipt', 'r.csv', repeat('f', 64), 1, 1, true)"
        ))
    with pytest.raises(IntegrityError):
        command.downgrade(alembic_config(), "0001")
    assert _version() == ["0002"]
    with get_direct_engine().connect() as c:
        assert c.scalar(text("SELECT count(*) FROM partners WHERE type = 'warehouse'")) == 1
