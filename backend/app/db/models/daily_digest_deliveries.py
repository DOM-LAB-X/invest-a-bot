from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin
from app.db.models.notification_deliveries import DeliveryChannel, DeliveryStatus


class DailyDigestDelivery(Base, TimestampMixin):
    """Mirrors NotificationDelivery but keyed on DailyDigest instead of Alert.

    Kept as a separate table (rather than making NotificationDelivery polymorphic)
    to avoid an 'exactly one of alert_id/daily_digest_id' invariant that's easy to
    violate at the ORM layer.
    """

    __tablename__ = "daily_digest_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "daily_digest_id",
            "channel",
            "destination_hash",
            name="uq_daily_digest_deliveries_digest_channel_dest",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    daily_digest_id: Mapped[int] = mapped_column(
        ForeignKey("daily_digests.id", ondelete="CASCADE"), nullable=False, index=True
    )

    channel: Mapped[DeliveryChannel] = mapped_column(
        SAEnum(DeliveryChannel, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    destination_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[DeliveryStatus] = mapped_column(
        SAEnum(DeliveryStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=DeliveryStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
