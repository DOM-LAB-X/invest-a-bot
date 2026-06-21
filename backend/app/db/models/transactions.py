from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
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


class Transaction(Base, TimestampMixin):
    """One parsed trade row extracted from a FilingDocument.

    `asset_name_raw` is the only required identifying field — many House filings
    don't cleanly map to a ticker (funds, bonds, options, ambiguous names), so
    `ticker` stays nullable and normalization happens downstream.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "filing_document_id",
            "source_transaction_id",
            name="uq_transactions_filing_doc_source_txn",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    filing_document_id: Mapped[int] = mapped_column(
        ForeignKey("filing_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    asset_name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    security_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    transaction_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    notification_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    amount_range_raw: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    capital_gains_over_200: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    description_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_extracted_fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parser_confidence: Mapped[float | None] = mapped_column(nullable=True)
    parser_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
