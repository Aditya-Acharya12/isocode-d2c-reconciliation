import hashlib
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.config import normalise_database_url
from app.core.database import get_engine
from app.models import AuditLog, Partner, SalesChannel

NEW_PARTNER = {
    "name": "Test Courier",
    "type": "courier",
    "settlement_terms_days": 10,
    "return_grace_days": 15,
}
PROBE = "/api/v1/diagnostics/upload-probe"


def test_create_partner_is_persisted_and_audited(founder_client, db, users):
    response = founder_client.post("/api/v1/partners", json=NEW_PARTNER)
    assert response.status_code == 201
    partner = db.get(Partner, response.json()["id"])
    assert partner.name == "Test Courier"

    log = db.scalar(select(AuditLog).where(AuditLog.action == "partner.create"))
    assert log.actor_id == users["founder"].id
    assert log.after["settlement_terms_days"] == 10


def test_duplicate_partner_name_is_a_conflict(founder_client):
    assert founder_client.post("/api/v1/partners", json=NEW_PARTNER).status_code == 201
    duplicate = founder_client.post("/api/v1/partners", json=NEW_PARTNER)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "partner_exists"


def test_partner_validation_errors_name_the_field(founder_client):
    response = founder_client.post(
        "/api/v1/partners", json={**NEW_PARTNER, "settlement_terms_days": -1, "type": "bank"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert {item["field"] for item in body["details"]} == {"settlement_terms_days", "type"}


def test_update_records_before_and_after(founder_client, db):
    partner_id = founder_client.post("/api/v1/partners", json=NEW_PARTNER).json()["id"]
    response = founder_client.patch(
        f"/api/v1/partners/{partner_id}", json={"settlement_terms_days": 30}
    )
    assert response.json()["settlement_terms_days"] == 30
    log = db.scalar(select(AuditLog).where(AuditLog.action == "partner.update"))
    assert log.before["settlement_terms_days"] == 10
    assert log.after["settlement_terms_days"] == 30


def test_update_with_no_change_writes_no_audit_row(founder_client, db):
    partner_id = founder_client.post("/api/v1/partners", json=NEW_PARTNER).json()["id"]
    founder_client.patch(f"/api/v1/partners/{partner_id}", json={"settlement_terms_days": 10})
    assert db.scalar(select(AuditLog).where(AuditLog.action == "partner.update")) is None


def test_update_of_a_missing_partner_is_not_found(founder_client):
    response = founder_client.patch("/api/v1/partners/999", json={"is_active": False})
    assert response.status_code == 404


def test_audit_row_and_change_commit_together(founder_client, db):
    """If the audit write fails, the change itself must not be saved."""
    with patch("app.api.routes.partners.record_audit", side_effect=RuntimeError("boom")):
        try:
            founder_client.post("/api/v1/partners", json=NEW_PARTNER)
        except RuntimeError:
            pass  # the test client re-raises server errors
    assert db.scalar(select(Partner).where(Partner.name == "Test Courier")) is None


def test_workspace_summary_counts_real_rows(finance_client, db):
    db.add(
        Partner(name="P", type="courier", settlement_terms_days=1, return_grace_days=1)
    )
    db.add(SalesChannel(code="WEB", name="Website"))
    db.commit()
    body = finance_client.get("/api/v1/workspace/summary").json()
    assert body == {
        "partners": 1,
        "sales_channels": 1,
        "uploaded_files": 0,
        "orders": 0,
        "reconciliation_runs": 0,
        "open_claims": 0,
    }


def test_healthz_is_public_and_never_touches_the_database(client):
    """Health checks must not wake Neon or use up its compute allowance."""
    with patch.object(get_engine(), "connect", side_effect=AssertionError("database used")):
        response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_database_outage_returns_a_retryable_error(finance_client):
    outage = OperationalError("SELECT 1", {}, Exception("connection refused"))
    with patch("app.api.routes.partners.select", side_effect=outage):
        response = finance_client.get("/api/v1/partners")
    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert response.headers["retry-after"] == "3"
    assert "connection refused" not in response.text


def test_responses_carry_security_headers(client):
    headers = client.get("/api/healthz").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def test_upload_probe_receives_the_whole_body_intact(finance_client):
    body = b"x" * 700_000
    response = finance_client.post(PROBE, content=body)
    assert response.status_code == 200
    assert response.json()["bytes_received"] == len(body)
    assert response.json()["sha256"] == hashlib.sha256(body).hexdigest()


def test_upload_probe_enforces_the_limit(finance_client):
    # MAX_UPLOAD_MB is 1 in tests.
    assert finance_client.post(PROBE, content=b"x" * (1024 * 1024 + 1)).status_code == 413


def test_upload_probe_enforces_the_limit_without_a_declared_length(finance_client):
    def chunks():
        for _ in range(3):
            yield b"x" * (512 * 1024)

    assert finance_client.post(PROBE, content=chunks()).status_code == 413


def test_upload_probe_is_hidden_unless_enabled(finance_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "enable_diagnostics", False)
    assert finance_client.post(PROBE, content=b"x").status_code == 404


def test_database_urls_are_normalised_for_the_driver():
    assert normalise_database_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalise_database_url("postgresql://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalise_database_url("postgresql+psycopg://u:p@h/d").count("psycopg") == 1
