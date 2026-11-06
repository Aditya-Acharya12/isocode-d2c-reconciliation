import pytest
from pydantic import ValidationError

from app.core.config import PLACEHOLDER_SECRET, Settings
from tests.conftest import assert_disposable_database

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
        Settings(**BASE, jwt_secret=GOOD_SECRET, app_env="production", cookie_secure=False)
    assert Settings(**BASE, jwt_secret=GOOD_SECRET, app_env="production", cookie_secure=True)


def test_direct_url_falls_back_to_the_main_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    settings = Settings(**BASE, jwt_secret=GOOD_SECRET, database_url_direct="")
    assert settings.sqlalchemy_direct_url == settings.sqlalchemy_url
