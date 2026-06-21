from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.db.models import Alert, AlertStatus, DeliveryStatus, NotificationDelivery
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationResult
from app.notifications.email import ResendEmailChannel
from app.notifications.slack import SlackWebhookChannel


@dataclass
class DispatchSummary:
    alerts_considered: int = 0
    deliveries_sent: int = 0
    deliveries_failed: int = 0
    deliveries_skipped_existing: int = 0
    channels_skipped_unconfigured: int = 0


def configured_channels(config: Settings = settings) -> list[NotificationChannel]:
    return [ResendEmailChannel(config=config), SlackWebhookChannel(config=config)]


def new_alerts_query(limit: int | None = None):
    statement = select(Alert).where(Alert.status == AlertStatus.NEW).order_by(Alert.matched_at.asc())
    if limit is not None:
        statement = statement.limit(limit)
    return statement


def existing_delivery(
    session: Session,
    *,
    alert: Alert,
    channel: NotificationChannel,
    destination: NotificationDestination,
) -> NotificationDelivery | None:
    return session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.alert_id == alert.id,
            NotificationDelivery.channel == channel.channel,
            NotificationDelivery.destination_hash == destination.destination_hash,
        )
    ).scalar_one_or_none()


def create_pending_delivery(
    alert: Alert,
    channel: NotificationChannel,
    destination: NotificationDestination,
) -> NotificationDelivery:
    return NotificationDelivery(
        alert_id=alert.id,
        channel=channel.channel,
        destination_label=destination.label,
        destination_hash=destination.destination_hash,
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        provider=channel.provider,
    )


def apply_result_to_delivery(delivery: NotificationDelivery, result: NotificationResult) -> None:
    now = datetime.now(timezone.utc)
    delivery.attempt_count = (delivery.attempt_count or 0) + 1
    delivery.request_payload = result.request_payload
    delivery.response_status_code = result.response_status_code
    delivery.response_body = result.response_body
    delivery.provider_message_id = result.provider_message_id
    delivery.error = result.error
    delivery.last_attempt_at = now
    if result.sent:
        delivery.status = DeliveryStatus.SENT
        delivery.sent_at = now
    else:
        delivery.status = DeliveryStatus.FAILED


def dispatch_alert(
    session: Session,
    alert: Alert,
    channels: list[NotificationChannel],
    *,
    retry_failed: bool = False,
) -> DispatchSummary:
    summary = DispatchSummary(alerts_considered=1)
    for channel in channels:
        destinations = channel.destinations()
        if not destinations:
            summary.channels_skipped_unconfigured += 1
            continue

        for destination in destinations:
            delivery = existing_delivery(session, alert=alert, channel=channel, destination=destination)
            if delivery is not None:
                if not (retry_failed and delivery.status == DeliveryStatus.FAILED):
                    summary.deliveries_skipped_existing += 1
                    continue
            else:
                delivery = create_pending_delivery(alert, channel, destination)
                session.add(delivery)
                session.flush()

            result = channel.send(alert, destination)
            apply_result_to_delivery(delivery, result)
            session.flush()
            if result.sent:
                summary.deliveries_sent += 1
            else:
                summary.deliveries_failed += 1
    return summary


def merge_summaries(target: DispatchSummary, source: DispatchSummary) -> DispatchSummary:
    target.alerts_considered += source.alerts_considered
    target.deliveries_sent += source.deliveries_sent
    target.deliveries_failed += source.deliveries_failed
    target.deliveries_skipped_existing += source.deliveries_skipped_existing
    target.channels_skipped_unconfigured += source.channels_skipped_unconfigured
    return target


def dispatch_new_alerts(
    session: Session,
    *,
    channels: list[NotificationChannel] | None = None,
    limit: int | None = None,
    retry_failed: bool = False,
) -> DispatchSummary:
    channels = channels if channels is not None else configured_channels()
    alerts = list(session.execute(new_alerts_query(limit)).scalars().all())
    summary = DispatchSummary()
    for alert in alerts:
        merge_summaries(
            summary,
            dispatch_alert(session, alert, channels, retry_failed=retry_failed),
        )
    return summary
