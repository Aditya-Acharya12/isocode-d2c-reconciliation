from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.api.deps import AUTH_COOKIE
from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models import AuditLog
from app.services.login_throttle import (
    MAX_FAILURES_PER_SOURCE,
    WINDOW_SECONDS,
    LoginThrottle,
)
from tests.conftest import FINANCE_EMAIL, PASSWORD, login

ME = "/api/v1/auth/me"


def test_passwords_are_hashed_with_argon2():
    hashed = hash_password("a-password")
    assert hashed.startswith("$argon2id$")
    assert "a-password" not in hashed
    assert verify_password("a-password", hashed)
    assert not verify_password("another-password", hashed)
    assert not verify_password("a-password", None)
    assert not verify_password("a-password", "not-a-hash")


def test_login_sets_a_protected_cookie_and_returns_the_session(client, users):
    response = login(client, FINANCE_EMAIL)
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "finance_executive"
    assert "password_hash" not in response.text

    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "path=/" in cookie_header


def test_email_is_case_insensitive(client, users):
    assert login(client, FINANCE_EMAIL.upper()).status_code == 200


def test_wrong_password_and_unknown_email_look_identical(client, users):
    wrong = login(client, FINANCE_EMAIL, "wrong-password")
    unknown = login(client, "nobody@test.example", "wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert AUTH_COOKIE not in wrong.cookies


def test_inactive_user_cannot_sign_in_and_existing_session_stops(finance_client, db, users):
    assert finance_client.get(ME).status_code == 200
    user = db.merge(users["finance"])
    user.is_active = False
    db.commit()

    assert finance_client.get(ME).status_code == 401
    assert login(finance_client, FINANCE_EMAIL).status_code == 401


def test_requests_without_a_cookie_are_refused(client):
    response = client.get(ME)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def _token(secret: str, **overrides) -> str:
    claims = {
        "sub": "1",
        "ver": 0,
        "exp": datetime.now(UTC) + timedelta(hours=1),
        **overrides,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def test_forged_and_expired_tokens_are_refused(client, users):
    secret = get_settings().jwt_secret
    user_id = str(users["finance"].id)
    bad_tokens = [
        _token("a-different-secret-of-sufficient-length!!", sub=user_id),
        _token(secret, sub=user_id, exp=datetime.now(UTC) - timedelta(seconds=1)),
        _token(secret, sub=user_id, ver=99),
        _token(secret, sub="not-a-number"),
        _token(secret, sub="999999"),
        jwt.encode({"sub": user_id, "ver": 0}, secret, algorithm="HS256"),  # no expiry
        jwt.encode(
            {"sub": user_id, "ver": 0, "exp": datetime.now(UTC) + timedelta(hours=1)},
            key="",
            algorithm="none",
        ),
        "garbage",
    ]
    for token in bad_tokens:
        client.cookies.set(AUTH_COOKIE, token)
        assert client.get(ME).status_code == 401, token
    client.cookies.set(AUTH_COOKIE, _token(secret, sub=user_id))
    assert client.get(ME).status_code == 200


def test_logout_invalidates_the_old_token(finance_client):
    old_token = finance_client.cookies[AUTH_COOKIE]
    assert finance_client.post("/api/v1/auth/logout").status_code == 204
    assert finance_client.get(ME).status_code == 401

    finance_client.cookies.set(AUTH_COOKIE, old_token)
    assert finance_client.get(ME).status_code == 401


def test_repeated_failures_are_throttled_even_with_the_right_password(client, users):
    for _ in range(MAX_FAILURES_PER_SOURCE):
        assert login(client, FINANCE_EMAIL, "wrong-password").status_code == 401
    blocked = login(client, FINANCE_EMAIL)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "too_many_attempts"


def test_throttle_window_expires():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    for _ in range(MAX_FAILURES_PER_SOURCE):
        throttle.record_failure("a@b.c", "1.1.1.1")
    assert throttle.is_blocked("a@b.c", "1.1.1.1")
    assert not throttle.is_blocked("a@b.c", "2.2.2.2")  # a different source isn't blocked
    now[0] = WINDOW_SECONDS + 1
    assert not throttle.is_blocked("a@b.c", "1.1.1.1")


def test_login_outcomes_are_audited_without_secrets(client, users, db):
    login(client, FINANCE_EMAIL, "wrong-password-value")
    login(client, FINANCE_EMAIL)
    logs = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [log.action for log in logs] == ["auth.login_failed", "auth.login"]
    assert "wrong-password-value" not in str(logs[0].after)
    assert PASSWORD not in str(logs[0].after)
