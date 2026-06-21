import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class AlertStatus(str, enum.Enum):
    NEW = "new"
    READ = "read"
    DISMISSED = "dismissed"


class Alert(Base, TimestampMixin):
    """One scored match between a ProfileRule and a Transaction.

    Carries a denormalized snapshot of the matched transaction/filing at match time
    so the alert stays meaningful even if the underlying transaction is later
    overwritten by a reparse (see Transaction's delete-and-replace persist behavior).
    """

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "profile_rule_id",
            "transaction_id",
            name="uq_alerts_profile_rule_transaction",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_rule_id: Mapped[int] = mapped_column(
        ForeignKey("profile_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filing_document_id: Mapped[int] = mapped_column(
        ForeignKey("filing_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AlertStatus.NEW,
    )
    score: Mapped[float] = mapped_column(nullable=False)
    reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    filer_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticker_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    asset_name_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_type_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_min_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_max_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    transaction_date_snapshot: Mapped[date | None] = mapped_column(Date, nullable=True)
    filed_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filing_delay_days_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_confidence_snapshot: Mapped[float | None] = mapped_column(nullable=True)
    needs_review_snapshot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_url_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
