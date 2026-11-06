from enum import StrEnum


class PartnerType(StrEnum):
    MARKETPLACE = "marketplace"
    QUICK_COMMERCE = "quick_commerce"
    COURIER = "courier"
    PAYMENT_GATEWAY = "payment_gateway"
    WAREHOUSE = "warehouse"


class PaymentMethod(StrEnum):
    COD = "cod"
    PREPAID = "prepaid"


class DeliveryStatus(StrEnum):
    CREATED = "created"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RTO = "rto"
    CUSTOMER_RETURN = "customer_return"
    CANCELLED = "cancelled"


class RateEvent(StrEnum):
    DELIVERED = "delivered"
    RTO = "rto"
    CUSTOMER_RETURN = "customer_return"


class ShipmentType(StrEnum):
    FORWARD = "forward"
    RTO = "rto"
    CUSTOMER_RETURN = "customer_return"


class RateCardStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class TaxKind(StrEnum):
    GST_TCS = "gst_tcs"
    INCOME_TAX_TDS = "income_tax_tds"


class FileType(StrEnum):
    ORDER = "order"
    SETTLEMENT = "settlement"
    COD_REMITTANCE = "cod_remittance"
    FREIGHT_INVOICE = "freight_invoice"
    RETURN_RECORD = "return_record"
    WAREHOUSE_RECEIPT = "warehouse_receipt"


class MappingSource(StrEnum):
    AI = "ai"
    HEURISTIC = "heuristic"
    MANUAL = "manual"


class IssueSeverity(StrEnum):
    ERROR = "error"  # the row isn't imported
    WARNING = "warning"  # the row is imported, or was deliberately left out, and says why
    DUPLICATE = "duplicate"  # the row repeats one already seen


class JobKind(StrEnum):
    FILE_ANALYSIS = "file_analysis"
    VALIDATION = "validation"
    IMPORT = "import"
    RECONCILIATION = "reconciliation"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MatchMethod(StrEnum):
    EXACT_ORDER_ID = "exact_order_id"
    NORMALISED_ORDER_ID = "normalised_order_id"
    TRACKING_ID = "tracking_id"
    PARTNER_REF = "partner_ref"
    HUMAN_REVIEW = "human_review"


class ReconStatus(StrEnum):
    SETTLED = "settled"
    SHORT_PAID = "short_paid"
    UNPAID_PAST_DUE = "unpaid_past_due"
    FREIGHT_OVERCHARGED = "freight_overcharged"
    RETURN_NOT_RECEIVED = "return_not_received"
    NEEDS_REVIEW = "needs_review"
    PENDING = "pending"


class DiscrepancyCategory(StrEnum):
    SHORT_PAID = "short_paid"
    UNPAID_PAST_DUE = "unpaid_past_due"
    FREIGHT_OVERCHARGED = "freight_overcharged"
    RETURN_NOT_RECEIVED = "return_not_received"
    NEEDS_REVIEW = "needs_review"


class DiscrepancyState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class SuggestionState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_RECOVERED = "partially_recovered"
    RECOVERED = "recovered"
    REJECTED = "rejected"
    WRITTEN_OFF = "written_off"
