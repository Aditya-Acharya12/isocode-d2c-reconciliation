"""Importing this package registers every table on Base.metadata."""

from app.models.base import Base
from app.models.config import (
    FreightRateRule,
    Partner,
    RateCard,
    SalesChannel,
    Setting,
    SettlementRateRule,
    TaxRule,
)
from app.models.identity import AuditLog, User
from app.models.ingestion import (
    BackgroundJob,
    ColumnMapping,
    ImportJob,
    ImportRowError,
    MappingVersion,
    UploadedFile,
    UploadedFileBlob,
)
from app.models.reconciliation import (
    Claim,
    ClaimEvent,
    Discrepancy,
    MatchSuggestion,
    ReconciliationResult,
    ReconciliationRun,
)
from app.models.records import (
    CodRemittance,
    FreightInvoiceLine,
    Order,
    ReturnRecord,
    SettlementLine,
    WarehouseReceipt,
)

__all__ = [
    "AuditLog",
    "BackgroundJob",
    "Base",
    "Claim",
    "ClaimEvent",
    "CodRemittance",
    "ColumnMapping",
    "Discrepancy",
    "FreightInvoiceLine",
    "FreightRateRule",
    "ImportJob",
    "ImportRowError",
    "MappingVersion",
    "MatchSuggestion",
    "Order",
    "Partner",
    "RateCard",
    "ReconciliationResult",
    "ReconciliationRun",
    "ReturnRecord",
    "SalesChannel",
    "SettlementLine",
    "SettlementRateRule",
    "Setting",
    "TaxRule",
    "UploadedFile",
    "UploadedFileBlob",
    "User",
    "WarehouseReceipt",
]
