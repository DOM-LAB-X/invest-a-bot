from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.db.models import DailyDigest, DailyDigestDelivery, DeliveryStatus, DigestStatus
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationResult
from app.notifications.digest_rendering import render_daily_digest_message
from app.services.notification_dispatch import configured_channels


@dataclass
class DigestDispatchSummary:
    digests_considered: int = 0
    deliveries_sent: int = 0
    deliveries_failed: int = 0
    deliveries_skipped_existing: int = 0
    channels_skipped_unconfigured: int = 0


def existing_digest_delivery(
    session: Session,
    *,
    digest: DailyDigest,
    channel: NotificationChannel,
    destination: NotificationDestination,
) -> DailyDigestDelivery | None:
    return session.execute(
        select(DailyDigestDelivery).where(
            DailyDigestDelivery.daily_digest_id == digest.id,
            DailyDigestDelivery.channel == channel.channel,
            DailyDigestDelivery.destination_hash == destination.destination_hash,
        )
    ).scalar_one_or_none()


def create_pending_digest_delivery(
    digest: DailyDigest,
    channel: NotificationChannel,
    destination: NotificationDestination,
) -> DailyDigestDelivery:
    return DailyDigestDelivery(
        daily_digest_id=digest.id,
        channel=channel.channel,
        destination_label=destination.label,
        destination_hash=destination.destination_hash,
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        provider=channel.provider,
    )


def apply_result_to_digest_delivery(delivery: DailyDigestDelivery, result: NotificationResult) -> None:
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


def update_digest_status(digest: DailyDigest, summary: DigestDispatchSummary) -> None:
    if summary.deliveries_failed and summary.deliveries_sent:
        digest.status = DigestStatus.PARTIAL
    elif summary.deliveries_failed:
        digest.status = DigestStatus.FAILED
    elif summary.deliveries_sent:
        digest.status = DigestStatus.SENT
        digest.sent_at = datetime.now(timezone.utc)


def dispatch_daily_digest(
    session: Session,
    digest: DailyDigest,
    *,
    channels: list[NotificationChannel] | None = None,
    config: Settings = settings,
    retry_failed: bool = False,
) -> DigestDispatchSummary:
    channels = channels if channels is not None else configured_channels(config)
    message = render_daily_digest_message(digest)
    summary = DigestDispatchSummary(digests_considered=1)
    for channel in channels:
        destinations = channel.destinations()
        if not destinations:
            summary.channels_skipped_unconfigured += 1
            continue

        for destination in destinations:
            delivery = existing_digest_delivery(session, digest=digest, channel=channel, destination=destination)
            if delivery is not None:
                if not (retry_failed and delivery.status == DeliveryStatus.FAILED):
                    summary.deliveries_skipped_existing += 1
                    continue
            else:
                delivery = create_pending_digest_delivery(digest, channel, destination)
                session.add(delivery)
                session.flush()

            result = channel.send_message(message, destination)
            apply_result_to_digest_delivery(delivery, result)
            session.flush()
            if result.sent:
                summary.deliveries_sent += 1
            else:
                summary.deliveries_failed += 1

    update_digest_status(digest, summary)
    session.flush()
    return summary
