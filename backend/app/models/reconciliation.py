from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_column
from app.models.enums import (
    ClaimStatus,
    DiscrepancyCategory,
    DiscrepancyState,
    JobStatus,
    ReconStatus,
    SuggestionState,
)

Money = Numeric(14, 2)


class ReconciliationRun(TimestampMixin, Base):
    """Records every input, so that any result can be explained later."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "recon_run_status"), default=JobStatus.QUEUED, nullable=False
    )
    background_job_id: Mapped[int] = mapped_column(
        ForeignKey("background_jobs.id"), unique=True, nullable=False
    )
    triggered_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    rate_card_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    timings_ms: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ReconciliationResult(CreatedAtMixin, Base):
    """Deliberately stored calculated data: the audit record of what was concluded and why."""

    __tablename__ = "reconciliation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "order_id"),
        # Each order has exactly one current result.
        Index(
            "uq_reconciliation_results_current_order",
            "order_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_reconciliation_results_current_status",
            "primary_status",
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    primary_status: Mapped[ReconStatus] = mapped_column(
        enum_column(ReconStatus, "recon_result_status"), nullable=False
    )
    expected_paise: Mapped[int | None] = mapped_column(BigInteger)
    actual_paise: Mapped[int | None] = mapped_column(BigInteger)
    # Tracked apart from the amounts above, and never part of money at risk.
    tcs_paise: Mapped[int | None] = mapped_column(BigInteger)
    tds_paise: Mapped[int | None] = mapped_column(BigInteger)
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Discrepancy(TimestampMixin, Base):
    """Long-lived across runs: a re-run updates the row and never duplicates it."""

    __tablename__ = "discrepancies"
    __table_args__ = (
        UniqueConstraint("order_id", "category", "counterparty_partner_id"),
        Index("ix_discrepancies_open_due", "due_date", postgresql_where=text("state = 'open'")),
        Index(
            "ix_discrepancies_open_partner",
            "counterparty_partner_id",
            postgresql_where=text("state = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    category: Mapped[DiscrepancyCategory] = mapped_column(
        enum_column(DiscrepancyCategory, "discrepancy_category"), nullable=False
    )
    counterparty_partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    expected: Mapped[Decimal | None] = mapped_column(Money)
    actual: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    due_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[DiscrepancyState] = mapped_column(
        enum_column(DiscrepancyState, "discrepancy_state"),
        default=DiscrepancyState.OPEN,
        nullable=False,
    )
    first_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_runs.id"), nullable=False
    )
    last_seen_run_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_runs.id"), nullable=False
    )


class MatchSuggestion(TimestampMixin, Base):
    """The human review queue for records with no order ID. Never auto-confirmed."""

    __tablename__ = "match_suggestions"
    __table_args__ = (
        UniqueConstraint("source_table", "source_id", "candidate_order_id"),
        Index(
            "ix_match_suggestions_pending", "created_at", postgresql_where=text("state = 'pending'")
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    candidate_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[SuggestionState] = mapped_column(
        enum_column(SuggestionState, "suggestion_state"),
        default=SuggestionState.PENDING,
        nullable=False,
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Claim(TimestampMixin, Base):
    """Amounts are snapshotted on purpose: what was filed must not change after later runs."""

    __tablename__ = "claims"
    __table_args__ = (
        # One active claim per discrepancy. A rejected claim doesn't block a new one.
        Index(
            "uq_claims_active_discrepancy",
            "discrepancy_id",
            unique=True,
            postgresql_where=text("status NOT IN ('rejected', 'written_off')"),
        ),
        Index("ix_claims_status", "status"),
        CheckConstraint("recovered_amount >= 0", name="recovered_not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    claim_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    discrepancy_id: Mapped[int] = mapped_column(ForeignKey("discrepancies.id"), nullable=False)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    category: Mapped[DiscrepancyCategory] = mapped_column(
        enum_column(DiscrepancyCategory, "claim_category"), nullable=False
    )
    expected: Mapped[Decimal | None] = mapped_column(Money)
    actual: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    recovered_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        enum_column(ClaimStatus, "claim_status"), default=ClaimStatus.DRAFT, nullable=False
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    # The calculation and source records as they were when the claim was filed.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class ClaimEvent(CreatedAtMixin, Base):
    __tablename__ = "claim_events"
    __table_args__ = (Index("ix_claim_events_claim", "claim_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ClaimStatus | None] = mapped_column(
        enum_column(ClaimStatus, "claim_event_from_status")
    )
    to_status: Mapped[ClaimStatus] = mapped_column(
        enum_column(ClaimStatus, "claim_event_to_status"), nullable=False
    )
    amount: Mapped[Decimal | None] = mapped_column(Money)
    note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
