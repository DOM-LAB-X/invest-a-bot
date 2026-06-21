from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyDigest
from app.db.session import get_db

router = APIRouter(prefix="/dashboard/daily-digests", tags=["dashboard"])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("", response_class=HTMLResponse)
def daily_digests_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    digests = list(
        db.execute(
            select(DailyDigest).order_by(
                DailyDigest.digest_date.desc(),
                DailyDigest.generated_at.desc().nullslast(),
                DailyDigest.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "daily_digests/index.html",
        {"active_nav": "digests", "digests": digests},
    )


@router.get("/{digest_id}", response_class=HTMLResponse)
def daily_digest_detail(digest_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    digest = db.get(DailyDigest, digest_id)
    if digest is None:
        raise HTTPException(status_code=404, detail="Daily digest not found")
    return templates.TemplateResponse(
        request,
        "daily_digests/detail.html",
        {"active_nav": "digests", "digest": digest, "payload": digest.payload or {}},
    )

