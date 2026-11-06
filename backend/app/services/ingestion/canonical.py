"""The canonical model: what each file type can contain, and how to recognise it.

Mirrors docs/architecture.md section 3. No heavy imports here, because the API loads
this at startup, and pandas should only be loaded when a job actually needs it.
"""

from dataclasses import dataclass, field

from app.models.enums import (
    DeliveryStatus,
    FileType,
    PartnerType,
    PaymentMethod,
    RateEvent,
    ShipmentType,
)

ID, TEXT, MONEY, DATE, INT, ENUM, REF = "id", "text", "money", "date", "int", "enum", "ref"

# Friendly format names mapped to strptime patterns.
DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "MM-DD-YYYY": "%m-%d-%Y",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD/MM/YY": "%d/%m/%y",
    "DD-MM-YY": "%d-%m-%y",
    "DD-Mon-YYYY": "%d-%b-%Y",
    "DD Mon YYYY": "%d %b %Y",
    "YYYY/MM/DD": "%Y/%m/%d",
    "YYYY-MM-DD HH:mm:ss": "%Y-%m-%d %H:%M:%S",
    "YYYY-MM-DD HH:mm": "%Y-%m-%d %H:%M",
    "DD/MM/YYYY HH:mm": "%d/%m/%Y %H:%M",
    "DD/MM/YYYY HH:mm:ss": "%d/%m/%Y %H:%M:%S",
    "DD-MM-YYYY HH:mm": "%d-%m-%Y %H:%M",
    "DD-MM-YYYY HH:mm:ss": "%d-%m-%Y %H:%M:%S",
    "YYYY-MM-DDTHH:mm:ss": "%Y-%m-%dT%H:%M:%S",
}


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = TEXT
    required: bool = False
    synonyms: tuple[str, ...] = ()
    # canonical value -> ways partners write it, compared after normalising
    enum_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    max_length: int = 64
    allow_negative: bool = True
    default: str | None = None  # used when the column isn't mapped at all
    db_column: str | None = None

    @property
    def column(self) -> str:
        return self.db_column or self.name


@dataclass(frozen=True)
class FileSchema:
    file_type: FileType
    label: str
    fields: tuple[Field, ...]
    # At least one field from each group must be mapped.
    any_of: tuple[tuple[str, ...], ...] = ()
    allowed_partner_types: tuple[PartnerType, ...] = ()
    uses_channel: bool = False

    def get(self, name: str) -> Field | None:
        return next((item for item in self.fields if item.name == name), None)


_ORDER_REF = Field(
    "order_id",
    "Order ID",
    ID,
    synonyms=(
        "order id", "order no", "order number", "order ref", "order reference", "ord id",
        "ord no", "sub order id", "suborder id", "sub order no", "customer order id",
        "marketplace order id", "merchant order id", "partner order id", "reference order",
    ),
    db_column="external_order_ref",
)
_TRACKING = Field(
    "tracking_id",
    "Tracking ID / AWB",
    ID,
    synonyms=(
        "awb", "awb no", "awb number", "awb num", "airway bill", "air waybill", "waybill",
        "way bill", "tracking", "tracking no", "tracking number", "tracking id", "shipment id",
        "consignment no", "docket no", "lrn",
    ),
)
_WEIGHT_SLAB = Field(
    "weight_slab",
    "Weight slab",
    synonyms=("weight slab", "wt slab", "slab", "weight bracket", "billing slab", "rate slab"),
    max_length=32,
)
_ZONE = Field(
    "zone", "Zone", synonyms=("zone", "delivery zone", "billing zone", "shipping zone"),
    max_length=32,
)
_UTR = Field(
    "utr", "UTR", synonyms=("utr", "utr no", "utr number", "bank reference", "bank ref"),
)
_QUANTITY = ("quantity", "qty", "units", "no of items", "item count", "pieces", "pcs")

_RETURN_TYPES = {
    RateEvent.RTO.value: ("rto", "return to origin", "rto delivered", "undelivered return"),
    RateEvent.CUSTOMER_RETURN.value: (
        "customer return", "cust return", "customer returned", "rvp", "reverse pickup",
        "return", "returned",
    ),
}

SCHEMAS: dict[FileType, FileSchema] = {
    FileType.ORDER: FileSchema(
        FileType.ORDER,
        "Orders",
        (
            Field("order_id", "Order ID", ID, True, _ORDER_REF.synonyms,
                  db_column="external_order_id"),
            Field(
                "order_date", "Order date", DATE, True,
                ("order date", "order dt", "ordered on", "date of order", "created at",
                 "order created", "purchase date", "order placed on", "date"),
            ),
            Field(
                "order_value", "Order value", MONEY, True,
                ("order value", "order amount", "order total", "total amount", "amount", "value",
                 "sale amount", "selling price", "invoice value", "gross amount", "total", "mrp"),
                allow_negative=False,
            ),
            Field(
                "payment_method", "Payment method", ENUM, True,
                ("payment method", "payment mode", "payment type", "pay mode", "mode of payment",
                 "cod prepaid", "payment"),
                {
                    PaymentMethod.COD.value: ("cod", "cash on delivery", "cash", "pay on delivery",
                                              "postpaid"),
                    PaymentMethod.PREPAID.value: ("prepaid", "ppd", "pre paid", "online", "paid",
                                                  "card", "upi", "netbanking"),
                },
            ),
            Field(
                "delivery_status", "Delivery status", ENUM, True,
                ("delivery status", "status", "order status", "shipment status",
                 "fulfilment status", "fulfillment status", "current status"),
                {
                    DeliveryStatus.CREATED.value: ("created", "new", "pending", "confirmed",
                                                   "processing", "packed", "ready to ship"),
                    DeliveryStatus.IN_TRANSIT.value: ("in transit", "shipped", "dispatched",
                                                      "out for delivery", "ofd", "picked up"),
                    DeliveryStatus.DELIVERED.value: ("delivered", "dlvd", "completed", "complete",
                                                     "fulfilled"),
                    DeliveryStatus.RTO.value: ("rto", "rto delivered", "returned to origin",
                                               "return to origin", "returned to warehouse",
                                               "rto initiated", "undelivered"),
                    DeliveryStatus.CUSTOMER_RETURN.value: ("customer return", "returned", "return",
                                                           "rvp", "return delivered"),
                    DeliveryStatus.CANCELLED.value: ("cancelled", "canceled", "void"),
                },
            ),
            _TRACKING,
            Field(
                "courier", "Courier", REF,
                synonyms=("courier", "courier name", "courier partner", "carrier", "logistics",
                          "logistics partner", "shipping partner", "shipped by"),
                db_column="courier_id",
            ),
            Field("delivered_at", "Delivered on", DATE,
                  synonyms=("delivered at", "delivered on", "delivery date", "date delivered",
                            "delivered date")),
            Field("returned_at", "Returned on", DATE,
                  synonyms=("returned at", "returned on", "return date", "rto date")),
            Field("weight_grams", "Weight (grams)", INT,
                  synonyms=("weight grams", "weight g", "weight gm", "weight gms", "wt grams",
                            "wt gm", "declared weight", "dead weight")),
            _WEIGHT_SLAB,
            _ZONE,
            Field("quantity", "Quantity", INT, synonyms=_QUANTITY),
        ),
        uses_channel=True,
    ),
    FileType.SETTLEMENT: FileSchema(
        FileType.SETTLEMENT,
        "Settlements",
        (
            Field("settlement_ref", "Settlement reference", ID, True,
                  ("settlement ref", "settlement id", "settlement no", "settlement reference",
                   "payout id", "payout ref", "payment id", "transaction id", "txn id",
                   "txn ref", "batch id"), max_length=128),
            Field("net_amount", "Net amount", MONEY, True,
                  ("net amount", "net settlement", "net payable", "settled amount",
                   "settlement amount", "net paid", "amount settled", "payout amount", "net",
                   "amount paid", "net settled")),
            Field("settlement_date", "Settlement date", DATE, True,
                  ("settlement date", "settled on", "payout date", "payment date", "credit date",
                   "settlement dt", "date")),
            _ORDER_REF,
            _TRACKING,
            Field("gross_amount", "Gross amount", MONEY,
                  synonyms=("gross amount", "gross", "sale value", "order value", "gross sale")),
            Field("commission", "Commission", MONEY,
                  synonyms=("commission", "commission amount", "marketplace fee", "referral fee",
                            "comm")),
            Field("fees", "Other fees", MONEY,
                  synonyms=("fees", "fee", "other fees", "platform fee", "fixed fee", "closing fee",
                            "gateway fee", "charges", "mdr")),
            Field("tcs_amount", "GST TCS", MONEY,
                  synonyms=("tcs", "tcs amount", "gst tcs", "tcs deducted", "tax collected")),
            Field("tds_amount", "Income tax TDS", MONEY,
                  synonyms=("tds", "tds amount", "income tax tds", "tds deducted", "194o", "194 o",
                            "tax deducted")),
            Field("transaction_type", "Transaction type",
                  synonyms=("transaction type", "txn type", "type", "entry type"), max_length=32),
            _UTR,
        ),
        any_of=(("order_id", "tracking_id"),),
        allowed_partner_types=(
            PartnerType.MARKETPLACE, PartnerType.QUICK_COMMERCE, PartnerType.PAYMENT_GATEWAY,
        ),
    ),
    FileType.COD_REMITTANCE: FileSchema(
        FileType.COD_REMITTANCE,
        "COD remittances",
        (
            Field("net_remittance", "Net remittance", MONEY, True,
                  ("net remittance", "net rem", "net remitted", "remitted amount",
                   "remittance amount", "amount remitted", "net amount", "net")),
            Field("remittance_date", "Remittance date", DATE, True,
                  ("remittance date", "remitted on", "rem date", "payment date", "date",
                   "transfer date")),
            _TRACKING,
            _ORDER_REF,
            Field("cod_amount", "COD collected", MONEY,
                  synonyms=("cod amount", "cod collected", "cod value", "collected amount",
                            "amount collected", "collection")),
            Field("cod_fee", "COD fee", MONEY,
                  synonyms=("cod fee", "cod charge", "cod charges", "collection fee",
                            "handling fee")),
            Field("remittance_ref", "Remittance reference", ID,
                  synonyms=("remittance ref", "remittance id", "remittance no", "batch id",
                            "crf id", "remittance reference"), max_length=128),
            _UTR,
        ),
        any_of=(("tracking_id", "order_id"),),
        allowed_partner_types=(PartnerType.COURIER,),
    ),
    FileType.FREIGHT_INVOICE: FileSchema(
        FileType.FREIGHT_INVOICE,
        "Freight invoices",
        (
            Field("invoice_no", "Invoice number", ID, True,
                  ("invoice no", "invoice number", "invoice id", "inv no", "bill no",
                   "invoice ref")),
            Field("tracking_id", "Tracking ID / AWB", ID, True, _TRACKING.synonyms),
            Field("total_charge", "Total charge", MONEY, True,
                  ("total charge", "total charges", "total freight", "total amount", "amount",
                   "invoice amount", "freight amount", "total", "billed amount")),
            Field("invoice_date", "Invoice date", DATE, True,
                  ("invoice date", "inv date", "bill date", "billing date", "date")),
            Field(
                "shipment_type", "Shipment type", ENUM,
                synonyms=("shipment type", "direction", "leg", "movement", "type",
                          "forward reverse"),
                enum_values={
                    ShipmentType.FORWARD.value: ("forward", "fwd", "outbound", "delivery"),
                    ShipmentType.RTO.value: ("rto", "return to origin"),
                    ShipmentType.CUSTOMER_RETURN.value: ("customer return", "reverse", "rvp",
                                                         "return"),
                },
                default=ShipmentType.FORWARD.value,
            ),
            _ORDER_REF,
            Field("billed_weight_grams", "Billed weight (grams)", INT,
                  synonyms=("billed weight", "billed weight grams", "charged weight",
                            "chargeable weight", "billed wt")),
            _WEIGHT_SLAB,
            _ZONE,
            Field("base_freight", "Base freight", MONEY,
                  synonyms=("base freight", "freight", "freight charge", "forward charge",
                            "base charge")),
            Field("cod_charge", "COD charge", MONEY,
                  synonyms=("cod charge", "cod charges", "cod fee", "cod handling")),
            Field("other_charges", "Other charges", MONEY,
                  synonyms=("other charges", "other charge", "fuel surcharge", "surcharge",
                            "fuel", "misc charges")),
            Field("gst_on_freight", "GST on freight", MONEY,
                  synonyms=("gst", "gst on freight", "gst amount", "igst", "tax", "tax amount")),
        ),
        allowed_partner_types=(PartnerType.COURIER,),
    ),
    FileType.RETURN_RECORD: FileSchema(
        FileType.RETURN_RECORD,
        "Returns",
        (
            Field("return_type", "Return type", ENUM, True,
                  ("return type", "type", "return reason type", "rto or return", "return mode"),
                  _RETURN_TYPES),
            Field("returned_at", "Returned on", DATE, True,
                  ("returned at", "returned on", "return date", "rto date", "rto delivered date",
                   "delivered to origin", "date")),
            _TRACKING,
            _ORDER_REF,
            Field("return_status", "Return status",
                  synonyms=("return status", "status", "rto status", "current status"),
                  max_length=32),
            Field("quantity", "Quantity", INT, synonyms=_QUANTITY),
            Field("return_value", "Return value", MONEY,
                  synonyms=("return value", "return amount", "refund amount", "value",
                            "order value")),
        ),
        any_of=(("tracking_id", "order_id"),),
        allowed_partner_types=(
            PartnerType.COURIER, PartnerType.MARKETPLACE, PartnerType.QUICK_COMMERCE,
        ),
    ),
    FileType.WAREHOUSE_RECEIPT: FileSchema(
        FileType.WAREHOUSE_RECEIPT,
        "Warehouse receipts",
        (
            Field("receipt_ref", "Receipt reference", ID, True,
                  ("receipt ref", "receipt id", "receipt no", "receipt number", "grn", "grn no",
                   "grn number", "inward id", "inward no"), max_length=128),
            Field("received_at", "Received on", DATE, True,
                  ("received at", "received on", "receipt date", "grn date", "inward date",
                   "received date", "date")),
            _TRACKING,
            _ORDER_REF,
            Field("quantity_received", "Quantity received", INT,
                  synonyms=("quantity received", "qty received", "received qty", "units received",
                            *_QUANTITY)),
            Field("condition", "Condition",
                  synonyms=("condition", "item condition", "qc status", "qc result", "grade"),
                  max_length=32),
        ),
        any_of=(("tracking_id", "order_id"),),
        allowed_partner_types=(PartnerType.WAREHOUSE,),
    ),
}


def schema_for(file_type: FileType) -> FileSchema:
    return SCHEMAS[file_type]
