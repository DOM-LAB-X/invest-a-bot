from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class EnrichmentItem(Base, TimestampMixin):
    """One normalized external item (SEC filing, later news/market data) tied to a
    ticker. Persisted (unlike daily digests) because source data and its
    interpretation don't change retroactively once fetched, and items are reused
    across alerts via the alert_enrichments join table.
    """

    __tablename__ = "enrichment_items"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_enrichment_items_source_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    form_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
