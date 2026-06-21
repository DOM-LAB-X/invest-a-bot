from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin
from app.db.models.enrichment_items import EnrichmentItem


class AlertEnrichment(Base, TimestampMixin):
    """Join table linking an Alert to a relevant EnrichmentItem, with a per-link
    relevance score/reason (the same EnrichmentItem can be relevant to multiple
    alerts, e.g. a same-ticker 8-K relevant to several politicians' trades).
    """

    __tablename__ = "alert_enrichments"
    __table_args__ = (
        UniqueConstraint("alert_id", "enrichment_item_id", name="uq_alert_enrichments_alert_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    enrichment_item_id: Mapped[int] = mapped_column(
        ForeignKey("enrichment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relevance_score: Mapped[float] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    enrichment_item: Mapped["EnrichmentItem"] = relationship("EnrichmentItem")
