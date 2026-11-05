from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_column
from app.models.enums import DeliveryStatus, MatchMethod, PaymentMethod, ShipmentType

Money = Numeric(14, 2)


class Order(TimestampMixin, Base):
    """Upserted, because a later order file legitimately updates delivery status."""

    __tablename__ = "orders"
    __table_args__ = (
        # Order IDs are only unique within a channel.
        UniqueConstraint("channel_id", "external_order_id"),
        CheckConstraint("order_value >= 0", name="value_not_negative"),
        Index("ix_orders_tracking_id", "tracking_id"),
        Index("ix_orders_status_date", "delivery_status", "order_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id"), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    order_value: Mapped[Decimal] = mapped_column(Money, nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_column(PaymentMethod, "order_payment_method"), nullable=False
    )
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        enum_column(DeliveryStatus, "order_delivery_status"), nullable=False
    )
    tracking_id: Mapped[str | None] = mapped_column(String(64))
    courier_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    delivered_at: Mapped[date | None] = mapped_column(Date)
    returned_at: Mapped[date | None] = mapped_column(Date)
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    weight_slab: Mapped[str | None] = mapped_column(String(32))
    zone: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[int | None] = mapped_column(Integer)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)


class TransactionMixin(CreatedAtMixin):
    """Shared by append-only partner records.

    The source file and row trace any figure back to the original upload.
    order_id stays null until the record is linked to an order.
    """

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tracking_id: Mapped[str | None] = mapped_column(String(64))
    external_order_ref: Mapped[str | None] = mapped_column(String(64))

    @declared_attr
    def partner_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("partners.id"), nullable=False)

    @declared_attr
    def order_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey("orders.id"), index=True)

    @declared_attr
    def source_file_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("uploaded_files.id"), nullable=False, index=True)

    @declared_attr
    def match_method(cls) -> Mapped[MatchMethod | None]:
        return mapped_column(enum_column(MatchMethod, f"{cls.__tablename__}_match_method"))


class SettlementLine(TransactionMixin, Base):
    __tablename__ = "settlement_lines"
    __table_args__ = (
        UniqueConstraint("partner_id", "dedupe_key"),
        Index("ix_settlement_lines_tracking_id", "tracking_id"),
        Index("ix_settlement_lines_external_order_ref", "external_order_ref"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    settlement_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    gross_amount: Mapped[Decimal | None] = mapped_column(Money)
    commission: Mapped[Decimal | None] = mapped_column(Money)
    fees: Mapped[Decimal | None] = mapped_column(Money)
    # Null means the file didn't report it, which is different from a reported zero.
    tcs_amount: Mapped[Decimal | None] = mapped_column(Money)
    tds_amount: Mapped[Decimal | None] = mapped_column(Money)
    transaction_type: Mapped[str | None] = mapped_column(String(32))
    utr: Mapped[str | None] = mapped_column(String(64))


class CodRemittance(TransactionMixin, Base):
    __tablename__ = "cod_remittances"
    __table_args__ = (
        UniqueConstraint("partner_id", "dedupe_key"),
        Index("ix_cod_remittances_tracking_id", "tracking_id"),
        Index("ix_cod_remittances_external_order_ref", "external_order_ref"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    remittance_date: Mapped[date] = mapped_column(Date, nullable=False)
    net_remittance: Mapped[Decimal] = mapped_column(Money, nullable=False)
    cod_amount: Mapped[Decimal | None] = mapped_column(Money)
    cod_fee: Mapped[Decimal | None] = mapped_column(Money)
    remittance_ref: Mapped[str | None] = mapped_column(String(128))
    utr: Mapped[str | None] = mapped_column(String(64))


class FreightInvoiceLine(TransactionMixin, Base):
    __tablename__ = "freight_invoice_lines"
    __table_args__ = (
        # Forward and RTO lines legitimately share an AWB.
        UniqueConstraint(
            "partner_id",
            "invoice_no",
            "tracking_id",
            "shipment_type",
            name="uq_freight_invoice_lines_natural_key",
        ),
        Index("ix_freight_invoice_lines_tracking_id", "tracking_id"),
        Index("ix_freight_invoice_lines_external_order_ref", "external_order_ref"),
    )

    invoice_no: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_charge: Mapped[Decimal] = mapped_column(Money, nullable=False)
    shipment_type: Mapped[ShipmentType] = mapped_column(
        enum_column(ShipmentType, "freight_line_shipment_type"),
        default=ShipmentType.FORWARD,
        nullable=False,
    )
    billed_weight_grams: Mapped[int | None] = mapped_column(Integer)
    weight_slab: Mapped[str | None] = mapped_column(String(32))
    zone: Mapped[str | None] = mapped_column(String(32))
    base_freight: Mapped[Decimal | None] = mapped_column(Money)
    cod_charge: Mapped[Decimal | None] = mapped_column(Money)
    other_charges: Mapped[Decimal | None] = mapped_column(Money)
    gst_on_freight: Mapped[Decimal | None] = mapped_column(Money)


class ReturnRecord(TransactionMixin, Base):
    __tablename__ = "return_records"
    __table_args__ = (
        UniqueConstraint("partner_id", "dedupe_key"),
        Index("ix_return_records_tracking_id", "tracking_id"),
        Index("ix_return_records_external_order_ref", "external_order_ref"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    return_type: Mapped[str] = mapped_column(String(32), nullable=False)
    returned_at: Mapped[date] = mapped_column(Date, nullable=False)
    return_status: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[int | None] = mapped_column(Integer)
    return_value: Mapped[Decimal | None] = mapped_column(Money)


class WarehouseReceipt(TransactionMixin, Base):
    __tablename__ = "warehouse_receipts"
    __table_args__ = (
        UniqueConstraint("receipt_ref"),
        Index("ix_warehouse_receipts_tracking_id", "tracking_id"),
        Index("ix_warehouse_receipts_external_order_ref", "external_order_ref"),
    )

    receipt_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_received: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str | None] = mapped_column(String(32))
