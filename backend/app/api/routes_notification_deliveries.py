from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NotificationDelivery
from app.db.session import get_db

router = APIRouter(prefix="/notification-deliveries", tags=["notification-deliveries"])


class NotificationDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    channel: str
    destination_label: str | None
    status: str
    attempt_count: int
    provider: str | None
    error: str | None
    sent_at: datetime | None


@router.get("", response_model=list[NotificationDeliveryRead])
def list_deliveries(db: Session = Depends(get_db)) -> list[NotificationDeliveryRead]:
    stmt = select(NotificationDelivery).order_by(NotificationDelivery.created_at.desc())
    return list(db.execute(stmt).scalars().all())
