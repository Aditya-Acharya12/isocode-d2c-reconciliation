"""Checks a hosted database without ever printing its connection strings.

Run through with_env.py, which supplies DATABASE_URL and DATABASE_URL_DIRECT:

    urls               Pooled and direct URLs point the right way; reports versions
    concurrent-migrate Starts several real migrators at once
    schema             The database matches the models, with the manual objects in place
    scratch-pytest     Runs pytest in a temporary database, and always deletes it
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
SCRATCH_DATABASE = "recon_verify_scratch"


def _url(name: str) -> URL:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} isn't set.")
    return make_url(value).set(drivername="postgresql+psycopg")


def _engine(url: URL, **kwargs):
    return create_engine(url, connect_args={"connect_timeout": 20}, **kwargs)


def _with_database(url: URL, database: str) -> str:
    return url.set(database=database).render_as_string(hide_password=False)


def command_urls() -> int:
    pooled, direct = _url("DATABASE_URL"), _url("DATABASE_URL_DIRECT")
    pooled_ok = "-pooler" in (pooled.host or "")
    direct_ok = "-pooler" not in (direct.host or "")
    same_endpoint = (pooled.host or "").replace("-pooler", "") == direct.host
    print(f"DATABASE_URL uses the pooler hostname:            {pooled_ok}")
    print(f"DATABASE_URL_DIRECT avoids the pooler hostname:   {direct_ok}")
    print(f"Both refer to the same endpoint:                  {same_endpoint}")
    print(f"Both use the same database and user:              "
          f"{pooled.database == direct.database and pooled.username == direct.username}")
    tls = all(url.query.get("sslmode") == "require" for url in (pooled, direct))
    print(f"TLS required on both:                             {tls}")

    for label, url, options in (
        ("pooled", pooled, {"prepare_threshold": None}),
        ("direct", direct, {}),
    ):
        started = time.perf_counter()
        engine = create_engine(url, connect_args={"connect_timeout": 20, **options})
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version")).scalar_one()
            # Asked of the client. pg_stat_ssl only sees the hop from Neon's proxy to Postgres.
            ssl = connection.connection.dbapi_connection.pgconn.ssl_in_use
            tables = connection.execute(
                text("SELECT count(*) FROM information_schema.tables "
                     "WHERE table_schema = 'public'")
            ).scalar_one()
        engine.dispose()
        elapsed = time.perf_counter() - started
        print(f"{label}: PostgreSQL {version}, TLS={ssl}, public tables={tables}, "
              f"connect and query {elapsed:.2f}s")

    with _engine(direct).connect() as connection:
        locked = connection.execute(text("SELECT pg_try_advisory_lock(1)")).scalar_one()
        connection.execute(text("SELECT pg_advisory_unlock(1)"))
    print(f"direct: session advisory lock works:              {locked}")
    return 0 if pooled_ok and direct_ok and same_endpoint else 1


def command_concurrent_migrate(count: int = 3) -> int:
    started = time.perf_counter()
    processes = [
        subprocess.Popen(  # noqa: S603 - fixed arguments
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(count)
    ]
    outputs = [process.communicate(timeout=240)[0] for process in processes]
    codes = [process.returncode for process in processes]
    applied = sum("Running upgrade" in output for output in outputs)
    print(f"Migrators started together: {count}")
    print(f"Exit codes: {codes}")
    print(f"Migrators that applied the migration: {applied} (the others found nothing to do)")
    print(f"Total time: {time.perf_counter() - started:.1f}s")
    for code, output in zip(codes, outputs, strict=True):
        if code != 0:
            print(output[-1500:])
    return 0 if all(code == 0 for code in codes) else 1


def command_schema() -> int:
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from app.models import Base

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()

    with _engine(_url("DATABASE_URL_DIRECT")).connect() as connection:
        rows = connection.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in rows]
        declared = {table.name for table in Base.metadata.sorted_tables}
        actual = set(inspect(connection).get_table_names())
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        extension = connection.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'")
        ).scalar_one()
        trigger = connection.execute(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_logs_append_only'")
        ).scalar_one()
        exclusion = connection.execute(
            text("SELECT count(*) FROM pg_constraint "
                 "WHERE conname = 'ex_rate_cards_published_dates'")
        ).scalar_one()
    print(f"Alembic version: {versions} (head is {head})")
    print(f"Declared tables present: {len(declared & actual)}/{len(declared)}")
    print(f"Differences from the models: {len(diff)}")
    print(f"btree_gist extension: {extension == 1}")
    print(f"Audit append-only trigger: {trigger == 1}")
    print(f"Rate card overlap constraint: {exclusion == 1}")
    ok = (versions == [head] and declared <= actual and not diff
          and extension == 1 and trigger == 1 and exclusion == 1)
    return 0 if ok else 1


def command_scratch_pytest(pytest_args: list[str]) -> int:
    pooled, direct = _url("DATABASE_URL"), _url("DATABASE_URL_DIRECT")
    admin = _engine(direct, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{SCRATCH_DATABASE}"'))
    print(f"Created temporary database {SCRATCH_DATABASE}", flush=True)
    try:
        env = {
            **os.environ,
            "TEST_DATABASE_URL": _with_database(pooled, SCRATCH_DATABASE),
            "TEST_DATABASE_URL_DIRECT": _with_database(direct, SCRATCH_DATABASE),
        }
        return subprocess.call(  # noqa: S603 - fixed program
            [sys.executable, "-m", "pytest", "-o", "addopts=", "--tb=short", *pytest_args],
            cwd=BACKEND_DIR,
            env=env,
        )
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)'))
            remaining = connection.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                {"name": SCRATCH_DATABASE},
            ).scalar_one()
        print(f"Temporary database deleted: {remaining == 0}", flush=True)


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "urls":
        return command_urls()
    if command == "concurrent-migrate":
        return command_concurrent_migrate()
    if command == "schema":
        return command_schema()
    if command == "scratch-pytest":
        return command_scratch_pytest(sys.argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
