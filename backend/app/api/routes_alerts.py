from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, AlertStatus
from app.db.session import get_db

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    profile_rule_id: int
    transaction_id: int
    filing_document_id: int
    status: AlertStatus
    score: float
    reasons: dict | None
    matched_at: datetime
    filer_name_snapshot: str | None
    ticker_snapshot: str | None
    asset_name_snapshot: str | None
    transaction_type_snapshot: str | None
    amount_min_snapshot: Decimal | None
    amount_max_snapshot: Decimal | None
    transaction_date_snapshot: date | None
    filed_at_snapshot: datetime | None
    filing_delay_days_snapshot: int | None
    parser_confidence_snapshot: float | None
    needs_review_snapshot: bool | None
    source_url_snapshot: str | None


def _alert_or_404(db: Session, alert_id: int) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.get("", response_model=list[AlertRead])
def list_alerts(
    status: AlertStatus | None = None,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[AlertRead]:
    statement = select(Alert).order_by(Alert.matched_at.desc())
    if status is not None:
        statement = statement.where(Alert.status == status)
    if profile_id is not None:
        statement = statement.where(Alert.profile_id == profile_id)
    return list(db.execute(statement).scalars().all())


@router.post("/{alert_id}/read", response_model=AlertRead)
def mark_alert_read(alert_id: int, db: Session = Depends(get_db)) -> AlertRead:
    alert = _alert_or_404(db, alert_id)
    alert.status = AlertStatus.READ
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/dismiss", response_model=AlertRead)
def dismiss_alert(alert_id: int, db: Session = Depends(get_db)) -> AlertRead:
    alert = _alert_or_404(db, alert_id)
    alert.status = AlertStatus.DISMISSED
    db.commit()
    db.refresh(alert)
    return alert
