import pytest
from pydantic import ValidationError

from app.core.config import PLACEHOLDER_SECRET, Settings

BASE = {"database_url": "postgresql://u:p@h/d", "_env_file": None}
GOOD_SECRET = "x" * 40


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
