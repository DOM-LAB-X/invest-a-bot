from __future__ import annotations

import httpx

from app.core.config import Settings, settings
from app.db.models import Alert, DeliveryChannel
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationResult, truncate_response_body
from app.notifications.rendering import render_slack_payload


class SlackWebhookChannel(NotificationChannel):
    channel = DeliveryChannel.SLACK
    provider = "slack"

    def __init__(
        self,
        *,
        config: Settings = settings,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=30.0)

    def destinations(self) -> list[NotificationDestination]:
        if not (self.config.notifications_enabled and self.config.slack_webhook_url):
            return []
        return [NotificationDestination(label="slack:webhook", hash_source=f"slack:{self.config.slack_webhook_url}")]

    def send(self, alert: Alert, destination: NotificationDestination) -> NotificationResult:
        if not self.config.slack_webhook_url:
            return NotificationResult(sent=False, error="Slack webhook channel is not configured")

        payload = render_slack_payload(alert)
        try:
            response = self.client.post(self.config.slack_webhook_url, json=payload)
            return NotificationResult(
                sent=response.is_success,
                request_payload=payload,
                response_status_code=response.status_code,
                response_body=truncate_response_body(response.text),
                error=None if response.is_success else response.text,
            )
        except httpx.HTTPError as exc:
            return NotificationResult(sent=False, request_payload=payload, error=str(exc))
