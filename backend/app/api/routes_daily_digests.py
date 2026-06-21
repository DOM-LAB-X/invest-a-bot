from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyDigest
from app.db.session import get_db

router = APIRouter(prefix="/daily-digests", tags=["daily-digests"])


class DailyDigestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    digest_date: date
    timezone: str
    scope: str
    profile_id: int | None
    status: str
    payload: dict | None
    generated_at: datetime | None
    sent_at: datetime | None


@router.get("", response_model=list[DailyDigestRead])
def list_daily_digests(db: Session = Depends(get_db)) -> list[DailyDigestRead]:
    stmt = select(DailyDigest).order_by(DailyDigest.digest_date.desc())
    return list(db.execute(stmt).scalars().all())
