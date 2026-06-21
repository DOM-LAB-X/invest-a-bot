from __future__ import annotations

import httpx

from app.core.config import Settings, settings
from app.db.models import Alert, DeliveryChannel
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationResult, truncate_response_body
from app.notifications.rendering import render_email_payload

RESEND_EMAILS_URL = "https://api.resend.com/emails"


class ResendEmailChannel(NotificationChannel):
    channel = DeliveryChannel.EMAIL
    provider = "resend"

    def __init__(
        self,
        *,
        config: Settings = settings,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=30.0)

    def destinations(self) -> list[NotificationDestination]:
        if not (
            self.config.notifications_enabled
            and self.config.resend_api_key
            and self.config.notification_from_email
            and self.config.notification_to_emails
        ):
            return []
        return [
            NotificationDestination(label=email, hash_source=f"email:{email.casefold()}")
            for email in self.config.notification_to_emails
        ]

    def send(self, alert: Alert, destination: NotificationDestination) -> NotificationResult:
        if not self.config.notification_from_email or not self.config.resend_api_key:
            return NotificationResult(sent=False, error="Resend email channel is not configured")

        payload = render_email_payload(
            alert,
            from_email=self.config.notification_from_email,
            to_email=destination.label,
        )
        try:
            response = self.client.post(
                RESEND_EMAILS_URL,
                headers={"Authorization": f"Bearer {self.config.resend_api_key}"},
                json=payload,
            )
            provider_message_id = None
            try:
                data = response.json()
                provider_message_id = data.get("id") if isinstance(data, dict) else None
            except ValueError:
                pass
            return NotificationResult(
                sent=response.is_success,
                request_payload=payload,
                response_status_code=response.status_code,
                response_body=truncate_response_body(response.text),
                provider_message_id=provider_message_id,
                error=None if response.is_success else response.text,
            )
        except httpx.HTTPError as exc:
            return NotificationResult(sent=False, request_payload=payload, error=str(exc))
