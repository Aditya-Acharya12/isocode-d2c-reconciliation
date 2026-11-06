"""Tests run against real Postgres, because constraints, partial indexes, triggers and
advisory locks are all part of what's being tested. The schema comes from the real
migrations, which also proves that those migrations work.
"""

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql://recon:recon_local_only@localhost:5433/recon_test"
)


def assert_disposable_database(url: str) -> None:
    """These tests drop the whole schema, so a real database must never reach them."""
    database = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        raise SystemExit(f"Refusing to run tests against database {database!r}.")


assert_disposable_database(os.environ["TEST_DATABASE_URL"])
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
# Set to run against a hosted pair of URLs. Session locks need the direct connection.
os.environ["DATABASE_URL_DIRECT"] = os.environ.get("TEST_DATABASE_URL_DIRECT", "")
assert_disposable_database(os.environ["DATABASE_URL_DIRECT"] or os.environ["DATABASE_URL"])
os.environ["JWT_SECRET"] = "test-only-secret-that-is-long-enough-for-validation"
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ENABLE_DIAGNOSTICS"] = "true"
os.environ["MAX_UPLOAD_MB"] = "1"
os.environ["DEMO_MODE"] = "true"
# Tests run jobs explicitly, so that each step can be checked in between.
os.environ["ENABLE_JOB_WORKER"] = "false"
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import get_direct_engine, get_engine  # noqa: E402
from app.core.permissions import Role  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services.login_throttle import login_throttle  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
FINANCE_EMAIL = "finance@test.example"
FOUNDER_EMAIL = "founder@test.example"
PASSWORD = "correct-horse-battery-staple"
# Hashing is deliberately slow, so one hash is shared across tests.
_PASSWORD_HASH = hash_password(PASSWORD)


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def reset_schema() -> None:
    with get_direct_engine().begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    reset_schema()
    command.upgrade(alembic_config(), "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database: None) -> Iterator[None]:
    yield
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with get_engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    login_throttle.reset()


@pytest.fixture
def db() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


@pytest.fixture
def users(db: Session) -> dict[str, User]:
    finance = User(
        email=FINANCE_EMAIL,
        full_name="Finance Test",
        password_hash=_PASSWORD_HASH,
        role=Role.FINANCE_EXECUTIVE,
    )
    founder = User(
        email=FOUNDER_EMAIL,
        full_name="Founder Test",
        password_hash=_PASSWORD_HASH,
        role=Role.CO_FOUNDER,
    )
    db.add_all([finance, founder])
    db.commit()
    return {"finance": finance, "founder": founder}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.fixture
def finance_client(users: dict[str, User]) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        assert login(test_client, FINANCE_EMAIL).status_code == 200
        yield test_client


@pytest.fixture
def founder_client(users: dict[str, User]) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        assert login(test_client, FOUNDER_EMAIL).status_code == 200
        yield test_client


@pytest.fixture
def sources(db: Session, users: dict[str, User]) -> dict[str, int]:
    from tests.ingestion_helpers import seed_sources

    return seed_sources(db)
