"""The assignment's three samples end to end: imported through the real pipeline,
reconciled by the real job, and read back through the API. Then claims on top."""

import csv
import io

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    Claim,
    ClaimEvent,
    Discrepancy,
    Partner,
    ReconciliationResult,
    SalesChannel,
    Setting,
)
from app.models.enums import PartnerType
from app.services.jobs import run_until_idle
from app.services.reconciliation.demo_rates import seed_demo_rate_card
from tests.ingestion_helpers import imported

AS_OF = "2026-11-07"

WEB_ORDERS = (
    b"Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Courier,Delivered On,Returned On,Wt_Slab,Qty\n"
    b"ORD-W-204118,01/10/2026,1499.00,COD,Delivered,AWB204118,Courier Aggregator A,05/10/2026,,0.5kg,1\n"
    b"ORD-W-204377,02/10/2026,2398.00,Prepaid,RTO,AWB204377,Courier Aggregator A,,10/10/2026,0.5kg,2\n"
    b"ORD-W-300001,03/10/2026,1000.00,Prepaid,Delivered,AWB300001,Courier Aggregator A,05/10/2026,,0.5kg,1\n"
    b"ORD-W-300002,30/10/2026,800.00,COD,Delivered,AWB300002,Courier Aggregator A,01/11/2026,,0.5kg,1\n"
    b"ORD-W-300003,01/09/2026,1500.00,Prepaid,RTO,AWB300003,Courier Aggregator A,,01/10/2026,0.5kg,1\n"
)
MKT_ORDERS = (
    b"Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Delivered On,Wt_Slab\n"
    b"ORD-M-88120,03/10/2026,1199.00,Prepaid,Delivered,AWB88120,06/10/2026,1kg\n"
)
MKT_SETTLEMENT = (
    b"Settlement ID,Sub Order ID,Net Paid,Settlement Date,TCS,TDS\n"
    b"SET-1,ORD-M-88120,855.09,20/10/2026,5.71,1.20\n"
)
GATEWAY_SETTLEMENT = (
    b"Settlement ID,Sub Order ID,Net Paid,Settlement Date\nPG-1,ORD-W-300001,980.00,07/10/2026\n"
)
FREIGHT = (
    b"Invoice No,AWB No,Total Charge,Invoice Date,Shipment Type,Wt_Slab\n"
    b"INV-1,AWB204377,142.00,15/10/2026,RTO,0.5kg\n"
    b"INV-1,AWB-NOBODY,50.00,15/10/2026,Forward,0.5kg\n"
)
RETURNS = (
    b"AWB No,Return Type,Return Date,Quantity\n"
    b"AWB204377,RTO,10/10/2026,2\n"
    b"AWB300003,RTO,01/10/2026,1\n"
)
RECEIPTS = b"GRN No,AWB No,Received On,Qty Received,Condition\nGRN-1,AWB204377,12/10/2026,2,Good\n"


@pytest.fixture
def world(db, users):
    partners = {
        name: Partner(name=name, type=kind, settlement_terms_days=terms, return_grace_days=grace)
        for name, kind, terms, grace in (
            ("Marketplace 1", PartnerType.MARKETPLACE, 15, 15),
            ("Courier Aggregator A", PartnerType.COURIER, 10, 15),
            ("Payment Gateway", PartnerType.PAYMENT_GATEWAY, 3, 15),
            ("Warehouse", PartnerType.WAREHOUSE, 0, 0),
        )
    }
    db.add_all(partners.values())
    db.flush()
    channels = {
        "WEB": SalesChannel(code="WEB", name="Website"),
        "MKT1": SalesChannel(
            code="MKT1", name="Marketplace 1", partner_id=partners["Marketplace 1"].id
        ),
    }
    db.add_all(channels.values())
    db.add(Setting(key="discrepancy_threshold_paise", value=1000))
    db.flush()
    seed_demo_rate_card(db)
    db.commit()
    return {
        "partners": {k: v.id for k, v in partners.items()},
        "channels": {k: v.id for k, v in channels.items()},
    }


def load_everything(client, world):
    p, c = world["partners"], world["channels"]
    imported(client, WEB_ORDERS, "web-orders.csv", "order", channel_id=c["WEB"])
    imported(client, MKT_ORDERS, "mkt-orders.csv", "order", channel_id=c["MKT1"])
    imported(
        client, MKT_SETTLEMENT, "mkt-settlement.csv", "settlement", partner_id=p["Marketplace 1"]
    )
    imported(client, GATEWAY_SETTLEMENT, "pg.csv", "settlement", partner_id=p["Payment Gateway"])
    imported(
        client, FREIGHT, "freight.csv", "freight_invoice", partner_id=p["Courier Aggregator A"]
    )
    imported(client, RETURNS, "returns.csv", "return_record", partner_id=p["Courier Aggregator A"])
    imported(client, RECEIPTS, "grn.csv", "warehouse_receipt", partner_id=p["Warehouse"])


def run(client, as_of=AS_OF) -> dict:
    response = client.post("/api/v1/reconciliation/runs", json={"as_of_date": as_of})
    assert response.status_code == 202, response.text
    run_until_idle()
    return client.get("/api/v1/reconciliation/runs").json()[0]


def rows(client, **params) -> dict:
    page = client.get("/api/v1/reconciliation/results", params={"limit": 100, **params}).json()
    return {item["external_order_id"]: item for item in page["items"]}


@pytest.fixture
def reconciled(finance_client, world):
    load_everything(finance_client, world)
    result = run(finance_client)
    assert result["status"] == "succeeded", result
    return result


# --- The three acceptance cases --------------------------------------------------------


def test_the_three_assignment_samples(finance_client, reconciled):
    by_id = rows(finance_client)
    sample_1, sample_2, sample_3 = (
        by_id["ORD-W-204118"],
        by_id["ORD-M-88120"],
        by_id["ORD-W-204377"],
    )

    assert sample_1["status"] == "unpaid_past_due"
    assert (sample_1["expected_paise"], sample_1["actual_paise"], sample_1["difference_paise"]) == (
        140900,
        0,
        -140900,
    )

    assert sample_2["status"] == "short_paid"
    assert (sample_2["expected_paise"], sample_2["actual_paise"], sample_2["difference_paise"]) == (
        89900,
        86200,
        -3700,
    )

    assert sample_3["status"] == "freight_overcharged"
    assert (sample_3["expected_paise"], sample_3["actual_paise"], sample_3["difference_paise"]) == (
        -9600,
        -14200,
        -4600,
    )
    assert sample_3["open_categories"] == ["freight_overcharged"]


def test_other_scenarios_in_the_same_run(finance_client, reconciled):
    by_id = rows(finance_client)
    assert by_id["ORD-W-300001"]["status"] == "settled"  # gateway paid ₹980, 2% fee
    assert by_id["ORD-W-300002"]["status"] == "pending"  # delivered 1 Nov, due 11 Nov
    assert by_id["ORD-W-300003"]["status"] == "return_not_received"  # no scan after 15 days


def test_the_explanation_traces_every_figure_to_its_source(finance_client, reconciled, world):
    order_id = rows(finance_client)["ORD-M-88120"]["order_id"]
    detail = finance_client.get(f"/api/v1/reconciliation/orders/{order_id}").json()
    receivable = detail["result"]["explanation"]["streams"]["receivable"]
    assert receivable["rule"]["pct_bps"] == 2500
    assert receivable["rule"]["rate_card"] == "Demo rate card v1"
    assert receivable["net_received_paise"] == 85509
    assert (receivable["tcs_paise"], receivable["tds_paise"]) == (571, 120)
    assert detail["result"]["tcs_paise"] == 571
    [settlement] = [r for r in detail["records"] if r["kind"] == "settlement"]
    assert settlement["filename"] == "mkt-settlement.csv"
    assert settlement["row"] == 2
    assert settlement["match_method"] == "exact_order_id"
    assert detail["order"]["filename"] == "mkt-orders.csv"
    assert detail["discrepancies"][0]["category"] == "short_paid"


def test_records_are_linked_by_order_id_then_tracking_and_never_guessed(
    finance_client, reconciled, db
):
    summary = finance_client.get("/api/v1/reconciliation/summary").json()
    assert summary["unmatched_records"]["freight"] == 1  # AWB-NOBODY matches no order
    unmatched = finance_client.get("/api/v1/reconciliation/unmatched").json()
    assert [(u["kind"], u["tracking_id"]) for u in unmatched] == [("freight", "AWB-NOBODY")]
    assert unmatched[0]["reason"].startswith("No order")
    assert reconciled["counts"]["linked"]["freight:tracking_id"] == 1
    assert reconciled["counts"]["linked"]["warehouse_receipt:tracking_id"] == 1


def test_summary_counts_and_money_at_risk(finance_client, reconciled):
    summary = finance_client.get("/api/v1/reconciliation/summary").json()
    assert summary["orders_total"] == summary["orders_evaluated"] == 6
    assert summary["reconciled"] == 1
    assert summary["open_discrepancies"] == 4
    assert summary["money_at_risk"] == "1492.00"  # 1,409 + 37 + 46: cash only
    assert summary["inventory_at_risk"] == "1500.00"  # the unscanned return, kept apart


def test_filters(finance_client, reconciled, world):
    assert set(rows(finance_client, status="short_paid")) == {"ORD-M-88120"}
    assert set(rows(finance_client, channel_id=world["channels"]["MKT1"])) == {"ORD-M-88120"}
    assert set(rows(finance_client, category="freight_overcharged")) == {"ORD-W-204377"}
    assert set(rows(finance_client, q="88120")) == {"ORD-M-88120"}
    assert set(rows(finance_client, date_from="2026-10-30")) == {"ORD-W-300002"}
    by_partner = rows(finance_client, partner_id=world["partners"]["Marketplace 1"])
    assert set(by_partner) == {"ORD-M-88120"}
    ordered = list(rows(finance_client, sort="difference"))
    assert ordered[:2] == ["ORD-W-300003", "ORD-W-204118"]  # ₹1,500 of stock, then ₹1,409


def test_returns_view_doesnt_trust_a_returned_status(finance_client, reconciled):
    page = finance_client.get("/api/v1/reconciliation/returns").json()
    by_id = {item["external_order_id"]: item for item in page["items"]}
    assert by_id["ORD-W-204377"]["received"] is True
    assert by_id["ORD-W-204377"]["receipt_reference"] == "GRN-1"
    assert by_id["ORD-W-300003"]["received"] is False
    assert by_id["ORD-W-300003"]["outcome"] == "return_not_received"
    assert by_id["ORD-W-300003"]["value_at_risk"] == "1500.00"
    assert by_id["ORD-W-300003"]["days_since_return"] == 37
    assert page["counts"] == {"settled": 1, "return_not_received": 1}
    only = finance_client.get("/api/v1/reconciliation/returns?state=not_received").json()
    assert [i["external_order_id"] for i in only["items"]] == ["ORD-W-300003"]


def test_tax_summary_is_separate_and_labelled_as_an_estimate(finance_client, reconciled):
    tax = finance_client.get("/api/v1/reconciliation/tax").json()
    marketplace = next(p for p in tax["partners"] if p["partner"] == "Marketplace 1")
    assert (marketplace["tcs"], marketplace["tds"]) == ("5.71", "1.20")
    gateway = next(p for p in tax["partners"] if p["partner"] == "Payment Gateway")
    assert gateway["lines_without_tax_reported"] == 1
    assert "not tax advice" in tax["note"]
    assert {r["kind"] for r in tax["configured_rates"]} == {"gst_tcs", "income_tax_tds"}


# --- Reruns ---------------------------------------------------------------------------


def test_rerunning_changes_nothing_and_duplicates_nothing(finance_client, reconciled, db):
    first = rows(finance_client)
    discrepancies_before = db.scalars(select(Discrepancy).order_by(Discrepancy.id)).all()
    second = run(finance_client)
    assert second["status"] == "succeeded"
    assert rows(finance_client) == first
    db.expire_all()
    after = db.scalars(select(Discrepancy).order_by(Discrepancy.id)).all()
    assert [(d.id, d.category, d.difference) for d in after] == [
        (d.id, d.category, d.difference) for d in discrepancies_before
    ]
    assert all(d.last_seen_run_id == second["id"] for d in after)
    current = db.scalar(
        select(func.count())
        .select_from(ReconciliationResult)
        .where(ReconciliationResult.is_current)
    )
    assert current == 6


def test_a_late_payment_resolves_the_discrepancy(finance_client, reconciled, world, db):
    remittance = (
        b"AWB_No,COD_Collected,Net_Rem,Remittance Date\nAWB204118,1499.00,1409.00,20/10/2026\n"
    )
    imported(
        finance_client,
        remittance,
        "late-cod.csv",
        "cod_remittance",
        partner_id=world["partners"]["Courier Aggregator A"],
    )
    run(finance_client)
    assert rows(finance_client)["ORD-W-204118"]["status"] == "settled"
    db.expire_all()
    unpaid = db.scalar(select(Discrepancy).where(Discrepancy.category == "unpaid_past_due"))
    assert unpaid.state == "resolved"
    assert finance_client.get("/api/v1/reconciliation/summary").json()["money_at_risk"] == "83.00"


def test_an_earlier_as_of_date_turns_overdue_into_pending(finance_client, world):
    load_everything(finance_client, world)
    run(finance_client, as_of="2026-10-10")
    assert rows(finance_client)["ORD-W-204118"]["status"] == "pending"  # due 15 Oct


def test_only_one_run_at_a_time(finance_client, world):
    first = finance_client.post("/api/v1/reconciliation/runs", json={"as_of_date": AS_OF}).json()
    second = finance_client.post("/api/v1/reconciliation/runs", json={"as_of_date": AS_OF}).json()
    assert first["created"] is True and second["created"] is False
    assert first["id"] == second["id"]
    assert finance_client.post(
        "/api/v1/reconciliation/runs", json={"as_of_date": "2099-01-01"}
    ).status_code in (202, 422)


def test_runs_are_audited(finance_client, reconciled, db):
    actions = db.scalars(
        select(AuditLog.action).where(AuditLog.entity_type == "reconciliation_run")
    ).all()
    assert actions == ["reconciliation.start", "reconciliation.run"]


# --- Claims ---------------------------------------------------------------------------


def test_eligible_discrepancies_are_grouped_by_partner(finance_client, reconciled):
    groups = {g["partner"]: g for g in finance_client.get("/api/v1/claims/eligible").json()}
    assert set(groups) == {"Courier Aggregator A", "Marketplace 1"}
    courier = groups["Courier Aggregator A"]
    assert sorted(i["category"] for i in courier["items"]) == [
        "freight_overcharged",
        "return_not_received",
        "unpaid_past_due",
    ]
    assert groups["Marketplace 1"]["total"] == "37.00"


def _claim_marketplace(client) -> dict:
    group = next(
        g for g in client.get("/api/v1/claims/eligible").json() if g["partner"] == "Marketplace 1"
    )
    response = client.post(
        "/api/v1/claims",
        json={"discrepancy_ids": [group["items"][0]["discrepancy_id"]], "note": "Short by ₹37"},
    )
    assert response.status_code == 201, response.text
    return response.json()[0]


def test_creating_a_claim_snapshots_its_evidence(finance_client, reconciled, db):
    claim = _claim_marketplace(finance_client)
    assert claim["claim_no"].startswith("CLM-")
    assert (claim["expected"], claim["actual"], claim["difference"], claim["claimed"]) == (
        "899.00",
        "862.00",
        "-37.00",
        "37.00",
    )
    assert claim["status"] == "draft"
    detail = finance_client.get(f"/api/v1/claims/{claim['id']}").json()
    assert detail["evidence"]["order"]["external_id"] == "ORD-M-88120"
    assert detail["evidence"]["records"][0]["source_file"] == "mkt-settlement.csv"
    assert "25%" in detail["calculation"] and "5.71 TCS" in detail["calculation"]
    assert detail["events"][0]["to_status"] == "draft"
    # It can't be claimed twice, and it's no longer offered.
    again = finance_client.post(
        "/api/v1/claims", json={"discrepancy_ids": [db.scalar(select(Claim.discrepancy_id))]}
    )
    assert again.status_code == 409
    groups = {g["partner"] for g in finance_client.get("/api/v1/claims/eligible").json()}
    assert "Marketplace 1" not in groups


def test_a_claims_evidence_doesnt_change_after_later_runs(finance_client, reconciled, world):
    claim = _claim_marketplace(finance_client)
    top_up = b"Settlement ID,Sub Order ID,Net Paid,Settlement Date,TCS,TDS\nSET-2,ORD-M-88120,37.00,25/10/2026,0,0\n"
    imported(
        finance_client,
        top_up,
        "top-up.csv",
        "settlement",
        partner_id=world["partners"]["Marketplace 1"],
    )
    run(finance_client)
    detail = finance_client.get(f"/api/v1/claims/{claim['id']}").json()
    assert detail["difference"] == "-37.00"  # as filed
    assert detail["discrepancy_state"] == "resolved"  # and the person can see it's since been paid


def test_evidence_csv_for_a_claim_and_for_a_partner(finance_client, reconciled, world):
    claim = _claim_marketplace(finance_client)
    response = finance_client.get(f"/api/v1/claims/{claim['id']}/evidence.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["evidence_type"] for r in table] == ["order", "settlement"]
    assert table[1]["source_file"] == "mkt-settlement.csv"
    assert table[1]["evidence_amount"] == "855.09"
    assert table[0]["claimed"] == "37.00"
    partner = finance_client.get(
        f"/api/v1/claims/evidence.csv?partner_id={world['partners']['Marketplace 1']}"
    )
    assert partner.status_code == 200
    assert "CLM-" in partner.text


def test_evidence_csv_cannot_carry_spreadsheet_formulas():
    from app.services.reconciliation.claims import _safe

    assert _safe('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert _safe("@SUM(1)") == "'@SUM(1)"
    assert _safe("-37.00") == "-37.00"  # a negative number stays a number


def test_claim_status_flow(finance_client, reconciled, db):
    claim_id = _claim_marketplace(finance_client)["id"]
    step = lambda to, **body: finance_client.post(  # noqa: E731
        f"/api/v1/claims/{claim_id}/transitions", json={"to_status": to, **body}
    )
    assert step("recovered").status_code == 409  # a draft must be submitted first
    assert step("submitted").json()["status"] == "submitted"
    assert step("partially_recovered").status_code == 422  # needs an amount
    assert step("partially_recovered", recovered_amount="50.00").status_code == 422  # too much
    assert step("partially_recovered", recovered_amount="20.00").json()["recovered"] == "20.00"
    assert step("partially_recovered", recovered_amount="10.00").status_code == 422  # can't go down
    done = step("recovered").json()
    assert (done["status"], done["recovered"]) == ("recovered", "37.00")
    assert done["allowed_transitions"] == []
    assert step("written_off", note="x").status_code == 409
    events = db.scalars(select(ClaimEvent.to_status).order_by(ClaimEvent.id)).all()
    assert events == ["draft", "submitted", "partially_recovered", "recovered"]


def test_rejecting_or_writing_off_needs_a_note(finance_client, reconciled):
    claim_id = _claim_marketplace(finance_client)["id"]
    url = f"/api/v1/claims/{claim_id}/transitions"
    finance_client.post(url, json={"to_status": "submitted"})
    assert finance_client.post(url, json={"to_status": "rejected"}).status_code == 422
    rejected = finance_client.post(
        url, json={"to_status": "rejected", "note": "Partner disputes it"}
    )
    assert rejected.json()["resolution_notes"] == "Partner disputes it"
    # A rejected claim frees the discrepancy for a fresh claim.
    groups = {g["partner"] for g in finance_client.get("/api/v1/claims/eligible").json()}
    assert "Marketplace 1" in groups


def test_resolved_discrepancies_cannot_be_claimed(finance_client, reconciled, world, db):
    remittance = (
        b"AWB_No,COD_Collected,Net_Rem,Remittance Date\nAWB204118,1499.00,1409.00,20/10/2026\n"
    )
    imported(
        finance_client,
        remittance,
        "late.csv",
        "cod_remittance",
        partner_id=world["partners"]["Courier Aggregator A"],
    )
    run(finance_client)
    resolved = db.scalar(select(Discrepancy.id).where(Discrepancy.state == "resolved"))
    response = finance_client.post("/api/v1/claims", json={"discrepancy_ids": [resolved]})
    assert response.status_code == 409


def test_recovery_reduces_money_at_risk(finance_client, reconciled):
    claim_id = _claim_marketplace(finance_client)["id"]
    url = f"/api/v1/claims/{claim_id}/transitions"
    finance_client.post(url, json={"to_status": "submitted"})
    finance_client.post(url, json={"to_status": "partially_recovered", "recovered_amount": "20.00"})
    assert finance_client.get("/api/v1/reconciliation/summary").json()["money_at_risk"] == "1472.00"


def test_the_co_founder_can_review_but_not_act(finance_client, founder_client, reconciled):
    claim = _claim_marketplace(finance_client)
    assert founder_client.get("/api/v1/reconciliation/summary").status_code == 200
    assert founder_client.get(f"/api/v1/claims/{claim['id']}").status_code == 200
    assert founder_client.get(f"/api/v1/claims/{claim['id']}/evidence.csv").status_code == 200
    assert founder_client.post("/api/v1/reconciliation/runs", json={}).status_code == 403
    assert founder_client.post("/api/v1/claims", json={"discrepancy_ids": [1]}).status_code == 403
    assert (
        founder_client.post(
            f"/api/v1/claims/{claim['id']}/transitions", json={"to_status": "submitted"}
        ).status_code
        == 403
    )
    assert founder_client.get("/api/v1/rate-cards").json()[0]["name"] == "Demo rate card"
