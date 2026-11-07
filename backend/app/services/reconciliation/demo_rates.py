"""The demo rate card and tax rules. Demo data, not engine logic.

The engine reads rates only from the database, so these can be changed or replaced
without touching any code. The values are invented (assumption A3), except where the
assignment's three sample orders imply them:

- Website COD, delivered: 6%, which gives ₹1,409 on ₹1,499 (ORD-W-204118)
- Marketplace 1 prepaid, delivered: 25%, which gives ₹899 on ₹1,199 (ORD-M-88120)
- Courier RTO freight, 0.5 kg slab: ₹96 (ORD-W-204377)
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    FreightRateRule,
    Partner,
    RateCard,
    SalesChannel,
    SettlementRateRule,
    TaxRule,
)
from app.models.enums import RateCardStatus

CARD_NAME = "Demo rate card"
EFFECTIVE_FROM = date(2026, 1, 1)
COURIER = None  # the order's own courier pays, as with COD remittances

# (channel code, payment method, event, payer partner, commission bps, fixed paise)
SETTLEMENT_RULES = [
    ("WEB", "cod", "delivered", COURIER, 600, 0),
    ("WEB", "prepaid", "delivered", "Payment Gateway", 200, 0),
    ("WEB", "cod", "rto", COURIER, 0, 0),
    ("WEB", "prepaid", "rto", "Payment Gateway", 0, 0),
    *[
        (code, payment, "delivered", name, 2500, 0)
        for code, name in (
            ("MKT1", "Marketplace 1"),
            ("MKT2", "Marketplace 2"),
            ("MKT3", "Marketplace 3"),
        )
        for payment in ("prepaid", "cod")
    ],
    *[
        (code, payment, "rto", name, 0, 0)
        for code, name in (
            ("MKT1", "Marketplace 1"),
            ("MKT2", "Marketplace 2"),
            ("MKT3", "Marketplace 3"),
        )
        for payment in ("prepaid", "cod")
    ],
    *[
        (code, payment, event, name, 1800 if event == "delivered" else 0, 0)
        for code, name in (("QC1", "Quick Commerce 1"), ("QC2", "Quick Commerce 2"))
        for payment in ("prepaid", "cod")
        for event in ("delivered", "rto")
    ],
]

# (slab, forward base paise, RTO base paise). COD adds 1.5%, with a ₹30 minimum.
FREIGHT_SLABS = [
    ("0.5kg", 4500, 9600),
    ("1kg", 6000, 11000),
    ("2kg", 9000, 15000),
    ("5kg", 15000, 22000),
]
COURIERS = ("Courier Aggregator A", "Courier Aggregator B")

# GST TCS and income tax TDS, on channels run by e-commerce operators (assumption A10).
TAX_CHANNELS = ("MKT1", "MKT2", "MKT3", "QC1", "QC2")
TAX_RULES = [("gst_tcs", 50), ("income_tax_tds", 10)]
TAX_FROM = date(2024, 10, 1)


def missing_references(db: Session) -> list[str]:
    """Channel codes and partner names the demo card refers to that don't exist."""
    channels = set(db.scalars(select(SalesChannel.code)))
    partners = set(db.scalars(select(Partner.name)))
    needed_channels = {code for code, *_ in SETTLEMENT_RULES} | set(TAX_CHANNELS)
    needed_partners = {payer for *_, payer, _, _ in SETTLEMENT_RULES if payer} | set(COURIERS)
    return sorted(needed_channels - channels) + sorted(needed_partners - partners)


def seed_demo_rate_card(db: Session) -> RateCard | None:
    """Adds the demo card if there's no card of that name yet. Never changes an existing one."""
    if db.scalar(select(RateCard).where(RateCard.name == CARD_NAME)) is not None:
        return None
    channels = {c.code: c.id for c in db.scalars(select(SalesChannel))}
    partners = {p.name: p.id for p in db.scalars(select(Partner))}
    card = RateCard(
        name=CARD_NAME, version=1, effective_from=EFFECTIVE_FROM, status=RateCardStatus.PUBLISHED
    )
    db.add(card)
    db.flush()
    for code, payment, event, payer, bps, fixed in SETTLEMENT_RULES:
        if code not in channels or (payer is not None and payer not in partners):
            continue
        db.add(
            SettlementRateRule(
                rate_card_id=card.id,
                channel_id=channels[code],
                payment_method=payment,
                event=event,
                payer_partner_id=partners.get(payer) if payer else None,
                pct_bps=bps,
                fixed_paise=fixed,
                round_to_paise=100,
            )
        )
    for courier in COURIERS:
        if courier not in partners:
            continue
        for slab, forward, rto in FREIGHT_SLABS:
            db.add(
                FreightRateRule(
                    rate_card_id=card.id,
                    courier_id=partners[courier],
                    weight_slab=slab,
                    shipment_type="forward",
                    base_paise=forward,
                    cod_pct_bps=150,
                    cod_min_paise=3000,
                )
            )
            db.add(
                FreightRateRule(
                    rate_card_id=card.id,
                    courier_id=partners[courier],
                    weight_slab=slab,
                    shipment_type="rto",
                    base_paise=rto,
                )
            )
    if db.scalar(select(TaxRule.id).limit(1)) is None:
        for code in TAX_CHANNELS:
            if code in channels:
                for kind, bps in TAX_RULES:
                    db.add(
                        TaxRule(
                            kind=kind,
                            channel_id=channels[code],
                            rate_bps=bps,
                            effective_from=TAX_FROM,
                        )
                    )
    db.flush()
    return card
