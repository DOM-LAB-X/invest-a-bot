import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class DigestScope(str, enum.Enum):
    PROFILE = "profile"
    CROSS_PROFILE = "cross_profile"


class DigestStatus(str, enum.Enum):
    GENERATED = "generated"
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"


class DailyDigest(Base, TimestampMixin):
    """One computed digest for a calendar day (in `timezone`), scoped either to a
    single profile or the cross-profile 'all new disclosures' view.

    Content is computed on the fly at generation time and snapshotted into `payload`
    so a digest's content doesn't silently change if queried again later (e.g. after
    a transaction is reparsed).
    """

    __tablename__ = "daily_digests"
    __table_args__ = (
        UniqueConstraint(
            "digest_date", "timezone", "scope", "profile_id", name="uq_daily_digests_date_tz_scope_profile"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    scope: Mapped[DigestScope] = mapped_column(
        SAEnum(DigestScope, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True
    )

    status: Mapped[DigestStatus] = mapped_column(
        SAEnum(DigestStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=DigestStatus.GENERATED,
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
