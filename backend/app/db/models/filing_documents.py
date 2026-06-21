import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class ParserStatus(str, enum.Enum):
    PENDING = "pending"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class FilingDocument(Base, TimestampMixin):
    """One row per raw disclosure document discovered from a source (e.g. House Clerk).

    This row must remain valid even if parsing fails — it is the auditable link back
    to the original public filing. Parsed trades live in `transactions`, never here.
    """

    __tablename__ = "filing_documents"
    __table_args__ = (
        UniqueConstraint("source", "source_document_id", name="uq_filing_documents_source_doc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    index_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    filing_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    filing_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    filer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    filer_bioguide_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    filer_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    filer_district: Mapped[str | None] = mapped_column(String(10), nullable=True)
    chamber: Mapped[str] = mapped_column(String(20), nullable=False)

    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pdf_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_index_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    parser_status: Mapped[ParserStatus] = mapped_column(
        SAEnum(ParserStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ParserStatus.PENDING,
    )
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parser_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parser_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parser_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_confidence: Mapped[float | None] = mapped_column(nullable=True)
    transaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
