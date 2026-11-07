"""The reconciliation rules on their own, with no database: every rule and boundary."""

from datetime import date

import pytest

from app.models.enums import DiscrepancyCategory, ReconStatus
from app.services.reconciliation import engine
from app.services.reconciliation.engine import (
    Context,
    FreightRule,
    OrderFacts,
    OrderRecords,
    Partner,
    SettlementRule,
    SourceRecord,
    evaluate,
    round_half_up,
)

AS_OF = date(2026, 11, 7)
WEB, MKT1 = 1, 2
COURIER, MARKETPLACE, GATEWAY, OTHER = 10, 20, 30, 40
PARTNERS = {
    COURIER: Partner(COURIER, "Courier A", settlement_terms_days=10, return_grace_days=15),
    MARKETPLACE: Partner(MARKETPLACE, "Marketplace 1", 15, 15),
    GATEWAY: Partner(GATEWAY, "Gateway", 3, 15),
    OTHER: Partner(OTHER, "Other", 5, 5),
}
RULES = [
    SettlementRule(1, "Demo v1", WEB, "cod", "delivered", None, 600, 0, 100),
    SettlementRule(2, "Demo v1", WEB, "prepaid", "delivered", GATEWAY, 200, 0, 100),
    SettlementRule(3, "Demo v1", WEB, "prepaid", "rto", GATEWAY, 0, 0, 100),
    SettlementRule(4, "Demo v1", MKT1, "prepaid", "delivered", MARKETPLACE, 2500, 0, 100),
    SettlementRule(5, "Demo v1", MKT1, "prepaid", "rto", MARKETPLACE, 0, 5000, 100),
]
FREIGHT = [
    FreightRule(1, "Demo v1", COURIER, "0.5kg", None, "forward", 4500, 150, 3000, 0),
    FreightRule(2, "Demo v1", COURIER, "0.5kg", None, "rto", 9600, 0, 0, 0),
    FreightRule(3, "Demo v1", COURIER, "1kg", None, "forward", 6000, 150, 3000, 0),
    FreightRule(4, "Demo v1", COURIER, "0.5kg", "D", "forward", 5500, 0, 0, 0),
]


def context(**overrides) -> Context:
    values = dict(
        as_of=AS_OF,
        threshold_paise=1000,
        partners=PARTNERS,
        settlement_rules=RULES,
        freight_rules=FREIGHT,
        tax_withholding_channels={MKT1},
    )
    return Context(**{**values, **overrides})


def order(**overrides) -> OrderFacts:
    values = dict(
        id=1,
        external_id="O1",
        channel_id=WEB,
        order_date=date(2026, 10, 1),
        value_paise=149900,
        payment_method="cod",
        delivery_status="delivered",
        delivered_at=date(2026, 10, 5),
        returned_at=None,
        courier_id=COURIER,
        weight_slab="0.5kg",
        zone=None,
        quantity=1,
        file_id=1,
        row=2,
    )
    return OrderFacts(**{**values, **overrides})


def money(kind, partner, amount, tcs=None, tds=None, **extra) -> SourceRecord:
    return SourceRecord(kind, 1, partner, 9, 3, "REF", date(2026, 10, 20), amount, tcs, tds, extra)


def freight(amount, shipment="forward", slab="0.5kg", gst=None, partner=COURIER, record_id=1):
    return SourceRecord(
        "freight",
        record_id,
        partner,
        8,
        4,
        "INV-1",
        date(2026, 10, 15),
        amount,
        extra={"shipment_type": shipment, "weight_slab": slab, "gst_paise": gst},
    )


# --- The assignment's three samples, at the rule level --------------------------------


def test_sample_1_website_cod_unpaid_past_due():
    result = evaluate(order(), OrderRecords(), context())
    assert result.primary_status == ReconStatus.UNPAID_PAST_DUE
    assert (result.expected_paise, result.actual_paise) == (140900, 0)
    [d] = result.discrepancies
    assert d.category == DiscrepancyCategory.UNPAID_PAST_DUE
    assert (d.counterparty_partner_id, d.difference_paise) == (COURIER, -140900)
    assert d.due_date == date(2026, 10, 15)  # delivered plus the courier's 10 days


def test_sample_2_marketplace_short_paid_with_tax_kept_apart():
    o = order(
        channel_id=MKT1,
        value_paise=119900,
        payment_method="prepaid",
        courier_id=None,
        delivered_at=date(2026, 10, 6),
    )
    paid = money("settlement", MARKETPLACE, 85509, tcs=571, tds=120)
    result = evaluate(o, OrderRecords(settlements=[paid]), context())
    assert result.primary_status == ReconStatus.SHORT_PAID
    assert (result.expected_paise, result.actual_paise) == (89900, 86200)
    assert result.discrepancies[0].difference_paise == -3700  # ₹37, not ₹43.91
    assert (result.tcs_paise, result.tds_paise) == (571, 120)


def test_sample_3_rto_freight_overcharged():
    o = order(
        value_paise=239800,
        payment_method="prepaid",
        delivery_status="rto",
        delivered_at=None,
        returned_at=date(2026, 10, 10),
        quantity=2,
    )
    records = OrderRecords(
        freight=[freight(14200, shipment="rto")],
        returns=[
            SourceRecord(
                "return", 1, COURIER, 7, 2, "AWB", date(2026, 10, 10), extra={"quantity": 2}
            )
        ],
        receipts=[
            SourceRecord(
                "warehouse_receipt",
                1,
                OTHER,
                6,
                2,
                "GRN",
                date(2026, 10, 12),
                extra={"quantity": 2, "condition": "Good"},
            )
        ],
    )
    result = evaluate(o, records, context())
    assert result.primary_status == ReconStatus.FREIGHT_OVERCHARGED
    assert (result.expected_paise, result.actual_paise) == (-9600, -14200)
    [d] = result.discrepancies
    assert d.category == DiscrepancyCategory.FREIGHT_OVERCHARGED
    assert d.difference_paise == -4600
    streams = result.explanation["streams"]
    assert streams["receivable"]["status"] == "settled"  # the gateway refund rule is ₹0
    assert streams["returns"]["status"] == "settled"  # scanned back in


# --- Arithmetic ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_paise_x10000", "step", "expected"),
    [
        (899_250_000, 100, 89900),
        (899_500_000, 100, 90000),
        (899_499_999, 100, 89900),
        (-959_200, 100, -100),
        (1_409_060_000, 100, 140900),
        (12345, 1, 1),
        (15000, 1, 2),
    ],
)
def test_rounding_is_half_up_and_symmetric(raw_paise_x10000, step, expected):
    assert round_half_up(raw_paise_x10000, step) == expected


def test_amounts_never_use_floats():
    result = evaluate(order(value_paise=33333), OrderRecords(), context())
    assert isinstance(result.expected_paise, int)


# --- Receivable ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("received", "status"),
    [
        (140900 - 1000, ReconStatus.SETTLED),  # exactly ₹10 short: within tolerance
        (140900 - 1001, ReconStatus.SHORT_PAID),  # ₹10.01 short
        (140900 + 1000, ReconStatus.SETTLED),
        (140900 + 1001, ReconStatus.NEEDS_REVIEW),
    ],  # overpaid: never leakage
)
def test_the_ten_rupee_threshold_is_strict(received, status):
    result = evaluate(
        order(), OrderRecords(remittances=[money("cod_remittance", COURIER, received)]), context()
    )
    assert result.primary_status == status
    assert bool(result.discrepancies) == (status == ReconStatus.SHORT_PAID)


def test_not_yet_due_is_pending_not_unpaid():
    o = order(delivered_at=date(2026, 11, 1))  # due 11 November
    assert evaluate(o, OrderRecords(), context()).primary_status == ReconStatus.PENDING
    assert (
        evaluate(o, OrderRecords(), context(as_of=date(2026, 11, 11))).primary_status
        == ReconStatus.PENDING
    )
    assert (
        evaluate(o, OrderRecords(), context(as_of=date(2026, 11, 12))).primary_status
        == ReconStatus.UNPAID_PAST_DUE
    )


def test_a_partial_payment_before_the_due_date_is_still_pending():
    o = order(delivered_at=date(2026, 11, 1))
    partial = OrderRecords(remittances=[money("cod_remittance", COURIER, 50000)])
    result = evaluate(o, partial, context())
    assert result.primary_status == ReconStatus.PENDING
    assert result.discrepancies == []


def test_several_payments_add_up():
    lines = [money("cod_remittance", COURIER, 100000), money("cod_remittance", COURIER, 40900)]
    assert (
        evaluate(order(), OrderRecords(remittances=lines), context()).primary_status
        == ReconStatus.SETTLED
    )


def test_a_refund_line_reduces_what_was_received():
    lines = [money("settlement", GATEWAY, 146902), money("settlement", GATEWAY, -20000)]
    o = order(payment_method="prepaid")
    result = evaluate(o, OrderRecords(settlements=lines), context())
    assert result.primary_status == ReconStatus.SHORT_PAID
    assert result.actual_paise == 126902


def test_prepaid_is_paid_by_settlement_and_cod_by_remittance():
    prepaid = order(payment_method="prepaid")  # 2% gateway fee: ₹1,469.02, rounded to ₹1,469
    settled = OrderRecords(settlements=[money("settlement", GATEWAY, 146900)])
    assert evaluate(prepaid, settled, context()).primary_status == ReconStatus.SETTLED
    wrong_route = OrderRecords(
        settlements=[money("settlement", GATEWAY, 146900)],
        remittances=[money("cod_remittance", COURIER, 100)],
    )
    result = evaluate(prepaid, wrong_route, context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW
    assert "unexpected route" in result.explanation["reason"]


def test_payment_from_an_unexpected_partner_needs_review():
    o = order(payment_method="prepaid")
    result = evaluate(o, OrderRecords(settlements=[money("settlement", OTHER, 146900)]), context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW


def test_missing_tax_columns_on_a_withholding_channel_isnt_leakage():
    o = order(channel_id=MKT1, value_paise=119900, payment_method="prepaid", courier_id=None)
    no_tax = OrderRecords(settlements=[money("settlement", MARKETPLACE, 85509)])
    result = evaluate(o, no_tax, context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW
    assert result.discrepancies == []
    assert "withholds tax" in result.explanation["reason"]


def test_reported_tax_counts_as_received_and_is_reported_separately():
    o = order(channel_id=MKT1, value_paise=119900, payment_method="prepaid", courier_id=None)
    exact = OrderRecords(settlements=[money("settlement", MARKETPLACE, 89209, tcs=571, tds=120)])
    result = evaluate(o, exact, context())
    assert result.primary_status == ReconStatus.SETTLED
    receivable = result.explanation["streams"]["receivable"]
    assert receivable["net_received_paise"] == 89209
    assert (receivable["tcs_paise"], receivable["tds_paise"]) == (571, 120)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (dict(channel_id=99), "No rate card rule"),
        (dict(delivered_at=None), "no delivery date"),
        (dict(courier_id=None), "no courier"),
    ],
)
def test_missing_inputs_need_review_rather_than_a_guess(overrides, reason):
    result = evaluate(order(**overrides), OrderRecords(), context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW
    assert reason in result.explanation["reason"]
    assert result.discrepancies == []


def test_two_rules_for_the_same_case_are_ambiguous():
    duplicate = [
        *RULES,
        SettlementRule(9, "Other card", WEB, "cod", "delivered", None, 500, 0, 100),
    ]
    result = evaluate(order(), OrderRecords(), context(settlement_rules=duplicate))
    assert result.primary_status == ReconStatus.NEEDS_REVIEW
    assert "ambiguous" in result.explanation["reason"]


def test_undelivered_and_cancelled_orders():
    assert (
        evaluate(
            order(delivery_status="in_transit", delivered_at=None), OrderRecords(), context()
        ).primary_status
        == ReconStatus.PENDING
    )
    assert (
        evaluate(order(delivery_status="cancelled"), OrderRecords(), context()).primary_status
        == ReconStatus.SETTLED
    )
    paid = OrderRecords(remittances=[money("cod_remittance", COURIER, 50000)])
    assert (
        evaluate(order(delivery_status="cancelled"), paid, context()).primary_status
        == ReconStatus.NEEDS_REVIEW
    )


def test_a_return_charge_not_yet_billed_is_pending():
    o = order(
        channel_id=MKT1,
        payment_method="prepaid",
        delivery_status="rto",
        delivered_at=None,
        returned_at=date(2026, 11, 1),
    )
    result = evaluate(o, OrderRecords(), context())
    assert result.explanation["streams"]["receivable"]["status"] == "pending"


def test_an_excess_return_charge_is_a_short_payment():
    o = order(
        channel_id=MKT1,
        payment_method="prepaid",
        delivery_status="rto",
        delivered_at=None,
        returned_at=date(2026, 11, 1),
    )
    charged = OrderRecords(settlements=[money("settlement", MARKETPLACE, -9000, tcs=0, tds=0)])
    stream = evaluate(o, charged, context()).explanation["streams"]["receivable"]
    assert stream["status"] == "short_paid"  # ₹90 charged against the ₹50 rule
    assert stream["difference_paise"] == -4000


# --- Freight ------------------------------------------------------------------------


def test_freight_within_threshold_is_settled():
    # COD adds max(1.5% of ₹1,499, ₹30) = ₹30: ₹45 + ₹30 = ₹75 expected.
    result = evaluate(
        order(),
        OrderRecords(
            remittances=[money("cod_remittance", COURIER, 140900)], freight=[freight(8500)]
        ),
        context(),
    )
    assert result.explanation["streams"]["freight"]["status"] == "settled"


def test_gst_on_freight_is_compared_separately():
    records = OrderRecords(
        remittances=[money("cod_remittance", COURIER, 140900)],
        freight=[freight(7500 + 1350, gst=1350)],
    )
    assert evaluate(order(), records, context()).primary_status == ReconStatus.SETTLED


def test_a_heavier_billed_slab_is_an_overcharge_and_says_why():
    records = OrderRecords(
        remittances=[money("cod_remittance", COURIER, 140900)], freight=[freight(9000, slab="1kg")]
    )
    result = evaluate(order(), records, context())
    assert result.primary_status == ReconStatus.FREIGHT_OVERCHARGED
    assert "Billed as slab 1kg" in result.explanation["reason"]


def test_a_zone_specific_rule_beats_a_general_one():
    records = OrderRecords(
        remittances=[money("cod_remittance", COURIER, 140900)], freight=[freight(5500)]
    )
    result = evaluate(order(zone="D"), records, context())
    assert result.explanation["streams"]["freight"]["lines"][0]["rule"]["id"] == 4


def test_the_same_shipment_billed_twice_needs_review():
    records = OrderRecords(
        remittances=[money("cod_remittance", COURIER, 140900)],
        freight=[freight(7500, record_id=1), freight(7500, record_id=2)],
    )
    result = evaluate(order(), records, context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW
    assert "billed twice" in result.explanation["reason"]


def test_freight_with_no_matching_rule_needs_review():
    records = OrderRecords(
        remittances=[money("cod_remittance", COURIER, 140900)], freight=[freight(7500, slab="20kg")]
    )
    result = evaluate(order(weight_slab=None), records, context())
    assert result.primary_status == ReconStatus.NEEDS_REVIEW


# --- Returns ------------------------------------------------------------------------


def rto(**overrides) -> OrderFacts:
    return order(
        **{
            "payment_method": "prepaid",
            "delivery_status": "rto",
            "delivered_at": None,
            "returned_at": date(2026, 10, 1),
            "value_paise": 239800,
            **overrides,
        }
    )


def test_a_return_with_no_warehouse_scan_after_the_grace_period():
    result = evaluate(rto(), OrderRecords(), context())
    assert result.primary_status == ReconStatus.RETURN_NOT_RECEIVED
    [d] = result.discrepancies
    assert d.category == DiscrepancyCategory.RETURN_NOT_RECEIVED
    assert d.difference_paise == -239800
    assert d.due_date == date(2026, 10, 16)  # 15 days' grace


def test_a_return_inside_the_grace_period_is_pending():
    result = evaluate(rto(returned_at=date(2026, 11, 1)), OrderRecords(), context())
    assert result.explanation["streams"]["returns"]["status"] == "pending"
    assert result.discrepancies == []


def test_a_marketplace_saying_returned_isnt_enough():
    """A return record from the courier or marketplace, but no warehouse scan."""
    said_returned = OrderRecords(
        returns=[
            SourceRecord(
                "return",
                1,
                COURIER,
                7,
                2,
                "AWB",
                date(2026, 10, 1),
                extra={"return_status": "Returned", "quantity": 1},
            )
        ]
    )
    result = evaluate(rto(), said_returned, context())
    assert result.primary_status == ReconStatus.RETURN_NOT_RECEIVED


@pytest.mark.parametrize(
    ("receipt", "status"),
    [
        ({"quantity": 1, "condition": "Good"}, "settled"),
        ({"quantity": 1, "condition": "Damaged"}, "needs_review"),
        ({"quantity": 1, "condition": "Good"}, "needs_review"),
    ],
)
def test_warehouse_receipts_are_checked(receipt, status):
    quantity = 2 if status == "needs_review" and receipt["condition"] == "Good" else 1
    scan = SourceRecord(
        "warehouse_receipt", 1, OTHER, 6, 2, "GRN", date(2026, 10, 3), extra=receipt
    )
    result = evaluate(rto(quantity=quantity), OrderRecords(receipts=[scan]), context())
    assert result.explanation["streams"]["returns"]["status"] == status


def test_a_return_with_no_date_needs_review():
    result = evaluate(rto(returned_at=None), OrderRecords(), context())
    assert result.explanation["streams"]["returns"]["status"] == "needs_review"


# --- Precedence and double counting ---------------------------------------------------


def test_status_precedence_order():
    assert [s.value for s in engine.PRECEDENCE] == [
        "unpaid_past_due",
        "return_not_received",
        "short_paid",
        "freight_overcharged",
        "needs_review",
        "pending",
        "settled",
    ]


def test_several_problems_keep_one_discrepancy_each_and_the_worst_status():
    records = OrderRecords(freight=[freight(20000)])
    result = evaluate(order(), records, context())  # unpaid, and freight overcharged
    assert result.primary_status == ReconStatus.UNPAID_PAST_DUE
    assert sorted(d.category.value for d in result.discrepancies) == [
        "freight_overcharged",
        "unpaid_past_due",
    ]
    assert len({(d.category, d.counterparty_partner_id) for d in result.discrepancies}) == 2


def test_the_explanation_records_rules_and_sources():
    result = evaluate(
        order(), OrderRecords(remittances=[money("cod_remittance", COURIER, 100000)]), context()
    )
    receivable = result.explanation["streams"]["receivable"]
    assert receivable["rule"] == {
        "id": 1,
        "rate_card": "Demo v1",
        "pct_bps": 600,
        "fixed_paise": 0,
        "round_to_paise": 100,
    }
    assert receivable["records"][0]["file_id"] == 9
    assert receivable["records"][0]["row"] == 3
    assert "short by ₹409.00" in result.explanation["reason"]
