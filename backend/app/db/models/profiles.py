from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class Profile(Base, TimestampMixin):
    """A user-created watch profile, made up of one or more ProfileRules (OR'd together)."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    rules: Mapped[list["ProfileRule"]] = relationship(
        "ProfileRule", cascade="all, delete-orphan", back_populates="profile"
    )


class ProfileRule(Base, TimestampMixin):
    """One rule under a profile. Conditions within a rule are AND'd; an empty
    condition means 'any' for that dimension. Multiple active rules under the
    same profile are OR'd together.
    """

    __tablename__ = "profile_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    profile: Mapped["Profile"] = relationship("Profile", back_populates="rules")

    filer_bioguide_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    filer_names: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    chambers: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    tickers: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    asset_keywords: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    sectors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    transaction_types: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    max_filing_delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_parser_confidence: Mapped[float] = mapped_column(nullable=False, default=0.85)
    include_needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
