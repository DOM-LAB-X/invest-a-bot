from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.db.models import Alert, DeliveryChannel


@dataclass(frozen=True)
class NotificationDestination:
    label: str
    hash_source: str

    @property
    def destination_hash(self) -> str:
        return hashlib.sha256(self.hash_source.encode("utf-8")).hexdigest()


@dataclass
class NotificationResult:
    sent: bool
    request_payload: dict[str, Any] | None = None
    response_status_code: int | None = None
    response_body: str | None = None
    provider_message_id: str | None = None
    error: str | None = None


class NotificationChannel(ABC):
    channel: DeliveryChannel
    provider: str

    @abstractmethod
    def destinations(self) -> list[NotificationDestination]:
        """Return configured destinations. Empty means skip without creating DB rows."""

    @abstractmethod
    def send(self, alert: Alert, destination: NotificationDestination) -> NotificationResult:
        """Send one alert to one destination."""


def truncate_response_body(value: str | None, max_length: int = 5000) -> str | None:
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]
