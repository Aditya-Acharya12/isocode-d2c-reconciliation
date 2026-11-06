import importlib.util
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import PLACEHOLDER_SECRET, Settings
from tests.conftest import BACKEND_DIR, assert_disposable_database

BASE = {"database_url": "postgresql://u:p@h/d", "_env_file": None}
GOOD_SECRET = "x" * 40


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@host/neondb?sslmode=require",
        "postgresql://u:p@host/recon",
        "postgresql://u:p@test.example.com/neondb",  # "test" in the host doesn't count
    ],
)
def test_the_suite_refuses_any_database_not_named_for_testing(url):
    with pytest.raises(SystemExit, match="Refusing"):
        assert_disposable_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost:5433/recon_test",
        "postgresql://u:p@host/recon_verify_scratch?sslmode=require",
    ],
)
def test_the_suite_accepts_disposable_databases(url):
    assert_disposable_database(url)


@pytest.mark.parametrize("secret", [PLACEHOLDER_SECRET, "too-short"])
def test_weak_jwt_secrets_stop_the_app_starting(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(**BASE, jwt_secret=secret)


def test_production_requires_secure_cookies(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        _production(cookie_secure=False)
    assert _production()


def test_direct_url_falls_back_to_the_main_url_outside_production(monkeypatch):
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    settings = Settings(**BASE, jwt_secret=GOOD_SECRET, database_url_direct="")
    assert settings.sqlalchemy_direct_url == settings.sqlalchemy_url


# --- Database URLs --------------------------------------------------------------------

# Each part is a marker, so that any leak of any part can be detected.
SECRET_USER = "LEAKUSER"
SECRET_PASSWORD = "LEAKPASSWORD"  # noqa: S105
POOLED_HOST = "ep-LEAKHOST-pooler.ap-southeast-1.aws.neon.tech"
DIRECT_HOST = "ep-LEAKHOST.ap-southeast-1.aws.neon.tech"
QUERY = "sslmode=require&channel_binding=require"
POOLED = f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@{POOLED_HOST}/neondb?{QUERY}"
DIRECT = f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@{DIRECT_HOST}/neondb?{QUERY}"
MARKERS = (SECRET_USER, SECRET_PASSWORD, "LEAKHOST", "neon.tech", "neondb")


def _production(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "production",
        "cookie_secure": True,
        "jwt_secret": GOOD_SECRET,
        "database_url": POOLED,
        "database_url_direct": DIRECT,
    }
    return Settings(**{**values, **overrides})


def _assert_nothing_leaked(exc: Exception) -> None:
    for text in (str(exc), repr(exc)):
        for marker in MARKERS:
            assert marker not in text, f"{marker} leaked into: {text}"


def test_correct_urls_are_accepted_and_the_driver_is_named():
    settings = _production()
    assert settings.sqlalchemy_url.startswith("postgresql+psycopg://")
    assert "-pooler" in settings.sqlalchemy_url
    assert "-pooler" not in settings.sqlalchemy_direct_url


def test_the_older_postgres_scheme_is_accepted():
    settings = _production(
        database_url=POOLED.replace("postgresql://", "postgres://"),
        database_url_direct=DIRECT.replace("postgresql://", "postgres://"),
    )
    assert settings.sqlalchemy_direct_url.startswith("postgresql+psycopg://")


def test_production_never_falls_back_from_the_direct_url():
    with pytest.raises(ValidationError, match="DATABASE_URL_DIRECT is empty") as caught:
        _production(database_url_direct="")
    assert "migrations and jobs" in str(caught.value)
    _assert_nothing_leaked(caught.value)


def test_a_missing_direct_variable_is_refused_in_production(monkeypatch):
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL_DIRECT is empty"):
        Settings(
            _env_file=None,
            app_env="production",
            cookie_secure=True,
            jwt_secret=GOOD_SECRET,
            database_url=POOLED,
        )


MALFORMED = [
    ("", "is empty"),
    (f'"{DIRECT}"', "wrapped in quotes"),
    (f"'{DIRECT}'", "wrapped in quotes"),
    (f"psql '{DIRECT}'", "psql command"),
    (f"DATABASE_URL_DIRECT={DIRECT}", "starts with a variable name"),
    (f"DATABASE_URL_DIRECT = {DIRECT}", "starts with a variable name"),
    (f" {DIRECT}", "spaces or a line break"),
    (f"{DIRECT} ", "spaces or a line break"),
    (f"{DIRECT}\n", "spaces or a line break"),
    ("<direct string>", "placeholder"),
    (DIRECT.replace("postgresql://", ""), "doesn't start with postgresql://"),
    (DIRECT.replace("postgresql://", "mysql://"), "doesn't start with postgresql://"),
    ("postgresql://", "has no host"),
    (f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@{DIRECT_HOST}:badport/neondb", "can't be parsed"),
    (f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@/neondb", "has no host"),
    (f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@{DIRECT_HOST}", "has no database name"),
]


@pytest.mark.parametrize(("value", "reason"), MALFORMED)
def test_malformed_direct_urls_are_named_without_being_repeated(value, reason):
    with pytest.raises(ValidationError) as caught:
        _production(database_url_direct=value)
    assert "DATABASE_URL_DIRECT " in str(caught.value)
    assert reason in str(caught.value)
    _assert_nothing_leaked(caught.value)


@pytest.mark.parametrize(("value", "reason"), MALFORMED)
def test_malformed_pooled_urls_are_named_without_being_repeated(value, reason):
    pooled = value.replace(DIRECT_HOST, POOLED_HOST).replace("DATABASE_URL_DIRECT", "DATABASE_URL")
    with pytest.raises(ValidationError) as caught:
        _production(database_url=pooled)
    assert "DATABASE_URL " in str(caught.value)
    assert reason in str(caught.value)
    _assert_nothing_leaked(caught.value)


def test_malformed_urls_are_refused_outside_production_too():
    with pytest.raises(ValidationError, match="DATABASE_URL_DIRECT is wrapped in quotes") as caught:
        Settings(
            _env_file=None,
            jwt_secret=GOOD_SECRET,
            database_url=DIRECT,
            database_url_direct=f'"{DIRECT}"',
        )
    _assert_nothing_leaked(caught.value)


def test_swapped_urls_are_detected():
    with pytest.raises(ValidationError, match="look swapped") as caught:
        _production(database_url=DIRECT, database_url_direct=POOLED)
    _assert_nothing_leaked(caught.value)


def test_a_pooled_url_in_the_direct_slot_is_detected():
    with pytest.raises(ValidationError, match="DATABASE_URL_DIRECT points at a pooled host") as e:
        _production(database_url_direct=POOLED)
    _assert_nothing_leaked(e.value)


def test_a_direct_neon_url_in_the_pooled_slot_is_detected():
    with pytest.raises(ValidationError, match="DATABASE_URL points at a direct Neon host") as e:
        _production(database_url=DIRECT)
    _assert_nothing_leaked(e.value)


def test_identical_urls_are_detected_on_any_host():
    same = f"postgresql://{SECRET_USER}:{SECRET_PASSWORD}@db.LEAKHOST.internal/neondb"
    with pytest.raises(ValidationError, match="are the same connection string") as e:
        _production(database_url=same, database_url_direct=same)
    _assert_nothing_leaked(e.value)


def test_hosts_other_than_neon_have_no_pooler_requirement():
    assert _production(
        database_url="postgresql://u:p@pooler.example.internal/app",
        database_url_direct="postgresql://u:p@db.example.internal/app",
    )


def test_a_weak_secret_error_does_not_echo_the_urls_either():
    with pytest.raises(ValidationError, match="JWT_SECRET") as caught:
        _production(jwt_secret="too-short")
    _assert_nothing_leaked(caught.value)
    assert "too-short" not in str(caught.value)


# --- The real start script, as the platform runs it ---------------------------------


def _run_start(**env) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "scripts/start.py"],
        cwd=BACKEND_DIR,
        env={
            **os.environ,
            "APP_ENV": "production",
            "COOKIE_SECURE": "true",
            "JWT_SECRET": GOOD_SECRET,
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"DATABASE_URL": POOLED, "DATABASE_URL_DIRECT": f"DATABASE_URL_DIRECT={DIRECT}"},
            "DATABASE_URL_DIRECT starts with a variable name",
        ),
        ({"DATABASE_URL": POOLED, "DATABASE_URL_DIRECT": ""}, "DATABASE_URL_DIRECT is empty"),
        ({"DATABASE_URL": DIRECT, "DATABASE_URL_DIRECT": POOLED}, "look swapped"),
        ({"DATABASE_URL": f"psql '{POOLED}'", "DATABASE_URL_DIRECT": DIRECT}, "psql command"),
    ],
)
def test_the_start_script_refuses_bad_urls_cleanly(env, expected):
    process = _run_start(**env)
    output = process.stdout + process.stderr
    assert process.returncode != 0
    assert expected in output
    assert "Traceback" not in output
    for marker in MARKERS:
        assert marker not in output, output


def test_a_final_connection_failure_does_not_expose_the_drivers_message(monkeypatch):
    spec = importlib.util.spec_from_file_location("start_script", BACKEND_DIR / "scripts/start.py")
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    driver_message = f'connection to {DIRECT_HOST} failed for user "{SECRET_USER}"'
    failure = OperationalError("SELECT 1", {}, Exception(driver_message))
    monkeypatch.setattr(start.time, "sleep", lambda _: None)
    monkeypatch.setattr(start.command, "upgrade", MagicMock(side_effect=failure))
    with pytest.raises(SystemExit) as caught:
        start.migrate()
    assert "Couldn't reach the database" in str(caught.value)
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    _assert_nothing_leaked(caught.value)
    assert start.command.upgrade.call_count == start.ATTEMPTS
