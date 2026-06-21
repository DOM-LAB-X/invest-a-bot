from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, AlertEnrichment, EnrichmentItem
from app.db.session import get_db

router = APIRouter(tags=["enrichments"])


class EnrichmentItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    external_id: str
    ticker: str | None
    cik: str | None
    event_type: str
    form_type: str | None
    title: str | None
    summary: str | None
    filed_at: datetime | None
    event_date: date | None
    url: str | None
    confidence: float | None


class AlertEnrichmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    relevance_score: float
    reason: str | None
    enrichment_item: EnrichmentItemRead


@router.get("/enrichments", response_model=list[EnrichmentItemRead])
def list_enrichments(ticker: str | None = None, db: Session = Depends(get_db)) -> list[EnrichmentItemRead]:
    stmt = select(EnrichmentItem).order_by(EnrichmentItem.filed_at.desc())
    if ticker:
        stmt = stmt.where(EnrichmentItem.ticker == ticker.upper())
    return list(db.execute(stmt).scalars().all())


@router.get("/alerts/{alert_id}/enrichments", response_model=list[AlertEnrichmentRead])
def list_alert_enrichments(alert_id: int, db: Session = Depends(get_db)) -> list[AlertEnrichmentRead]:
    if db.get(Alert, alert_id) is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    stmt = (
        select(AlertEnrichment)
        .where(AlertEnrichment.alert_id == alert_id)
        .order_by(AlertEnrichment.relevance_score.desc())
    )
    return list(db.execute(stmt).scalars().all())
