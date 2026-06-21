from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Alert, AlertEnrichment, AlertStatus
from app.db.session import get_db

router = APIRouter(prefix="/dashboard/alerts", tags=["dashboard"])
templates = Jinja2Templates(directory="app/web/templates")


def _alert_or_none(db: Session, alert_id: int) -> Alert | None:
    return db.get(Alert, alert_id)


def alert_enrichments_by_alert_id(db: Session, alert_ids: list[int]) -> dict[int, list[AlertEnrichment]]:
    if not alert_ids:
        return {}
    rows = (
        db.execute(
            select(AlertEnrichment)
            .where(AlertEnrichment.alert_id.in_(alert_ids))
            .options(selectinload(AlertEnrichment.enrichment_item))
            .order_by(AlertEnrichment.relevance_score.desc())
        )
        .scalars()
        .all()
    )
    grouped: dict[int, list[AlertEnrichment]] = defaultdict(list)
    for row in rows:
        grouped[row.alert_id].append(row)
    return dict(grouped)


@router.get("", response_class=HTMLResponse)
def alerts_page(
    request: Request,
    status: AlertStatus | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    statement = select(Alert).order_by(Alert.matched_at.desc())
    if status is not None:
        statement = statement.where(Alert.status == status)
    alerts = list(db.execute(statement).scalars().all())
    enrichments_by_alert_id = alert_enrichments_by_alert_id(db, [alert.id for alert in alerts])
    return templates.TemplateResponse(
        request,
        "alerts/index.html",
        {
            "active_nav": "alerts",
            "alerts": alerts,
            "selected_status": status.value if status else "",
            "statuses": list(AlertStatus),
            "enrichments_by_alert_id": enrichments_by_alert_id,
        },
    )


@router.post("/{alert_id}/read", response_class=RedirectResponse)
def mark_read(alert_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    alert = _alert_or_none(db, alert_id)
    if alert is not None:
        alert.status = AlertStatus.READ
        db.commit()
    return RedirectResponse(url="/dashboard/alerts", status_code=303)


@router.post("/{alert_id}/dismiss", response_class=RedirectResponse)
def dismiss(alert_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    alert = _alert_or_none(db, alert_id)
    if alert is not None:
        alert.status = AlertStatus.DISMISSED
        db.commit()
    return RedirectResponse(url="/dashboard/alerts", status_code=303)

