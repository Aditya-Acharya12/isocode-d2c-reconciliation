"""Roles must be enforced by the API. Every protected route is listed here, and a
separate test fails if a route is added without being listed.
"""

import pytest
from sqlalchemy import func, select

from app.core.permissions import ROLE_PERMISSIONS, Permission, Role
from app.main import app
from app.models import Partner

NEW_PARTNER = {
    "name": "Test Courier",
    "type": "courier",
    "settlement_terms_days": 10,
    "return_grace_days": 15,
}

# (method, path, json body, finance allowed, founder allowed)
ACCESS_MATRIX = [
    ("GET", "/api/v1/workspace/summary", None, True, True),
    ("GET", "/api/v1/partners", None, True, True),
    ("POST", "/api/v1/partners", NEW_PARTNER, False, True),
    ("PATCH", "/api/v1/partners/{partner_id}", {"return_grace_days": 5}, False, True),
    ("GET", "/api/v1/audit-logs", None, True, True),
    ("POST", "/api/v1/diagnostics/upload-probe", None, True, False),
    ("GET", "/api/v1/auth/me", None, True, True),
    ("POST", "/api/v1/auth/logout", None, True, True),
]
PUBLIC_ROUTES = {("POST", "/api/v1/auth/login"), ("GET", "/api/healthz")}


def _send(client, method, path, body, partner_id):
    url = path.replace("{partner_id}", str(partner_id))
    return client.request(method, url, json=body) if body else client.request(method, url)


@pytest.fixture
def partner(db):
    row = Partner(
        name="Existing Partner", type="marketplace", settlement_terms_days=7, return_grace_days=7
    )
    db.add(row)
    db.commit()
    return row


def test_every_route_is_covered_by_the_access_matrix():
    declared = {(method, path) for method, path, *_ in ACCESS_MATRIX} | PUBLIC_ROUTES
    # The OpenAPI schema lists every route, including those in included routers.
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert actual == declared


@pytest.mark.parametrize(("method", "path", "body", "_finance", "_founder"), ACCESS_MATRIX)
def test_anonymous_requests_are_refused(client, partner, method, path, body, _finance, _founder):
    assert _send(client, method, path, body, partner.id).status_code == 401


@pytest.mark.parametrize(("method", "path", "body", "allowed", "_founder"), ACCESS_MATRIX)
def test_finance_executive_access(finance_client, partner, method, path, body, allowed, _founder):
    status = _send(finance_client, method, path, body, partner.id).status_code
    assert (status == 403) is (not allowed), status


@pytest.mark.parametrize(("method", "path", "body", "_finance", "allowed"), ACCESS_MATRIX)
def test_co_founder_access(founder_client, partner, method, path, body, _finance, allowed):
    status = _send(founder_client, method, path, body, partner.id).status_code
    assert (status == 403) is (not allowed), status


def test_refused_write_changes_nothing(finance_client, db):
    assert finance_client.post("/api/v1/partners", json=NEW_PARTNER).status_code == 403
    assert db.scalar(select(func.count()).select_from(Partner)) == 0


def test_role_comes_from_the_database_not_the_token(finance_client, db, users):
    """A role change applies at once, even to a session that's already open."""
    assert finance_client.post("/api/v1/partners", json=NEW_PARTNER).status_code == 403
    user = db.merge(users["finance"])
    user.role = Role.CO_FOUNDER
    db.commit()
    assert finance_client.post("/api/v1/partners", json=NEW_PARTNER).status_code == 201


def test_separation_of_duties_in_the_permission_matrix():
    finance = ROLE_PERMISSIONS[Role.FINANCE_EXECUTIVE]
    founder = ROLE_PERMISSIONS[Role.CO_FOUNDER]
    # The person reconciling can't change what counts as leakage.
    assert Permission.MANAGE_CONFIG not in finance
    # The co-founder sees claims and reports, but doesn't operate the workflow.
    assert Permission.OPERATE_RECONCILIATION not in founder
    assert Permission.MANAGE_CLAIMS not in founder


def test_audit_log_scope_differs_by_role(finance_client, founder_client):
    finance_view = finance_client.get("/api/v1/audit-logs").json()
    founder_view = founder_client.get("/api/v1/audit-logs").json()
    assert {item["actor_email"] for item in finance_view["items"]} == {"finance@test.example"}
    assert {item["actor_email"] for item in founder_view["items"]} == {
        "finance@test.example",
        "founder@test.example",
    }
