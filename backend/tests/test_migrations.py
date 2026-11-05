"""Runs real Alembic processes, including at the same time, as a deploy would."""

import os
import subprocess
import sys
import threading
import time

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.core.database import get_engine
from app.models import Base
from tests.conftest import BACKEND_DIR, alembic_config, reset_schema

MIGRATION_LOCK_KEY = 738201946


@pytest.fixture
def empty_schema():
    """Starts empty, and leaves the fully migrated schema for other tests."""
    reset_schema()
    yield
    reset_schema()
    command.upgrade(alembic_config(), "head")


def _alembic_upgrade() -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603 - fixed arguments
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_concurrent_startup_migrations_all_succeed(empty_schema):
    """Without the lock, these would collide creating the same tables."""
    processes = [_alembic_upgrade() for _ in range(4)]
    outcomes = [(process.wait(timeout=120), process.stdout.read()) for process in processes]
    for exit_code, output in outcomes:
        assert exit_code == 0, output

    tables = set(inspect(get_engine()).get_table_names())
    assert {table.name for table in Base.metadata.sorted_tables} <= tables
    with get_engine().connect() as connection:
        versions = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    assert [row[0] for row in versions] == [_head_revision()]


def test_a_second_migrator_waits_for_the_lock(empty_schema):
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        process = _alembic_upgrade()
        time.sleep(4)
        assert process.poll() is None, "the migrator should be waiting for the lock"
        assert "alembic_version" not in inspect(get_engine()).get_table_names()
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})

    assert process.wait(timeout=120) == 0, process.stdout.read()
    assert "alembic_version" in inspect(get_engine()).get_table_names()


def test_a_crashed_migrator_does_not_leave_the_lock_held(empty_schema):
    """Postgres releases session locks when the connection ends."""
    release = threading.Event()

    def hold_then_disconnect() -> None:
        connection = get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        release.wait(timeout=30)
        connection.invalidate()  # drops the socket, as a crash would
        connection.close()

    holder = threading.Thread(target=hold_then_disconnect)
    holder.start()
    time.sleep(1)
    process = _alembic_upgrade()
    time.sleep(1)
    release.set()
    holder.join()
    assert process.wait(timeout=120) == 0, process.stdout.read()


def test_upgrade_is_a_no_op_when_already_at_head():
    process = _alembic_upgrade()
    assert process.wait(timeout=120) == 0, process.stdout.read()


def test_downgrade_removes_everything_and_upgrade_restores_it(empty_schema):
    config = alembic_config()
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    remaining = set(inspect(get_engine()).get_table_names()) - {"alembic_version"}
    assert remaining == set()
    command.upgrade(config, "head")
    assert "orders" in inspect(get_engine()).get_table_names()
