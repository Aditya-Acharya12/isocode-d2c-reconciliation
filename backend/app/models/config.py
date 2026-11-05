from datetime import date
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_column
from app.models.enums import (
    PartnerType,
    PaymentMethod,
    RateCardStatus,
    RateEvent,
    ShipmentType,
    TaxKind,
)


class Partner(TimestampMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (
        CheckConstraint("settlement_terms_days >= 0", name="terms_not_negative"),
        CheckConstraint("return_grace_days >= 0", name="grace_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    type: Mapped[PartnerType] = mapped_column(
        enum_column(PartnerType, "partner_type"), nullable=False
    )
    settlement_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    return_grace_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SalesChannel(TimestampMixin, Base):
    __tablename__ = "sales_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Null for the brand's own website, which has no marketplace partner.
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))


class RateCard(TimestampMixin, Base):
    """Published cards are immutable, and a change creates a new version."""

    __tablename__ = "rate_cards"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="valid_dates"
        ),
        # Two published versions of a card can never apply on the same date.
        ExcludeConstraint(
            ("name", "="),
            (
                text("daterange(effective_from, effective_to, '[]')"),
                "&&",
            ),
            where=text("status = 'published'"),
            using="gist",
            name="ex_rate_cards_published_dates",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[RateCardStatus] = mapped_column(
        enum_column(RateCardStatus, "rate_card_status"),
        default=RateCardStatus.DRAFT,
        nullable=False,
    )


class SettlementRateRule(TimestampMixin, Base):
    __tablename__ = "settlement_rate_rules"
    __table_args__ = (
        UniqueConstraint(
            "rate_card_id",
            "channel_id",
            "payment_method",
            "event",
            name="uq_settlement_rate_rules_dimensions",
        ),
        CheckConstraint("pct_bps >= 0", name="pct_not_negative"),
        CheckConstraint("fixed_paise >= 0", name="fixed_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_card_id: Mapped[int] = mapped_column(
        ForeignKey("rate_cards.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id"), nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_column(PaymentMethod, "settlement_rule_payment_method"), nullable=False
    )
    event: Mapped[RateEvent] = mapped_column(
        enum_column(RateEvent, "settlement_rule_event"), nullable=False
    )
    # Null means the money comes from the order's courier (COD on the website).
    payer_partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    pct_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fixed_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    round_to_paise: Mapped[int] = mapped_column(Integer, default=100, nullable=False)


class FreightRateRule(TimestampMixin, Base):
    __tablename__ = "freight_rate_rules"
    __table_args__ = (
        # zone may be null ("any zone"), and null must still count as a duplicate.
        UniqueConstraint(
            "rate_card_id",
            "courier_id",
            "weight_slab",
            "zone",
            "shipment_type",
            name="uq_freight_rate_rules_dimensions",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("base_paise >= 0", name="base_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_card_id: Mapped[int] = mapped_column(
        ForeignKey("rate_cards.id", ondelete="CASCADE"), nullable=False
    )
    courier_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    weight_slab: Mapped[str] = mapped_column(String(32), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(32))
    shipment_type: Mapped[ShipmentType] = mapped_column(
        enum_column(ShipmentType, "freight_rule_shipment_type"), nullable=False
    )
    base_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cod_pct_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cod_min_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fuel_pct_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TaxRule(TimestampMixin, Base):
    """Used only to sanity-check reported tax. Never used to calculate leakage."""

    __tablename__ = "tax_rules"
    __table_args__ = (
        UniqueConstraint("kind", "channel_id", "effective_from"),
        Index("ix_tax_rules_lookup", "channel_id", "kind", "effective_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[TaxKind] = mapped_column(enum_column(TaxKind, "tax_kind"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id"), nullable=False)
    rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Setting(TimestampMixin, Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
