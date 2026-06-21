from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.db.models import DailyDigest, DailyDigestDelivery, DeliveryChannel, DeliveryStatus, DigestScope, DigestStatus
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationMessage, NotificationResult
from app.services.digest_dispatch import dispatch_daily_digest


def digest() -> DailyDigest:
    return DailyDigest(
        id=1,
        digest_date=date(2026, 6, 21),
        timezone="America/New_York",
        scope=DigestScope.CROSS_PROFILE,
        payload={"transaction_count": 0},
        status=DigestStatus.GENERATED,
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flush_count = 0

    def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1


@dataclass
class _FakeChannel(NotificationChannel):
    channel: DeliveryChannel = DeliveryChannel.EMAIL
    provider: str = "fake"
    configured: bool = True
    sent: bool = True
    send_calls: int = 0
    last_message: NotificationMessage | None = None

    def destinations(self) -> list[NotificationDestination]:
        if not self.configured:
            return []
        return [NotificationDestination(label="user@example.test", hash_source="email:user@example.test")]

    def send_message(self, message: NotificationMessage, destination: NotificationDestination) -> NotificationResult:
        self.send_calls += 1
        self.last_message = message
        return NotificationResult(
            sent=self.sent,
            request_payload={"subject": message.subject},
            response_status_code=200 if self.sent else 500,
            response_body="ok" if self.sent else "bad",
            provider_message_id="digest-1" if self.sent else None,
            error=None if self.sent else "bad",
        )


def test_dispatch_daily_digest_skips_unconfigured_channel_without_row() -> None:
    session = _FakeSession()
    channel = _FakeChannel(configured=False)

    summary = dispatch_daily_digest(session, digest(), channels=[channel])

    assert summary.channels_skipped_unconfigured == 1
    assert session.added == []
    assert channel.send_calls == 0


def test_dispatch_daily_digest_creates_sent_delivery_and_marks_digest_sent() -> None:
    session = _FakeSession()
    channel = _FakeChannel(sent=True)
    daily_digest = digest()

    summary = dispatch_daily_digest(session, daily_digest, channels=[channel])

    assert summary.deliveries_sent == 1
    assert daily_digest.status == DigestStatus.SENT
    assert daily_digest.sent_at is not None
    delivery = session.added[0]
    assert isinstance(delivery, DailyDigestDelivery)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.provider_message_id == "digest-1"
    assert channel.last_message is not None
    assert "Daily congressional trade digest" in channel.last_message.subject


def test_dispatch_daily_digest_records_failure() -> None:
    session = _FakeSession()
    channel = _FakeChannel(sent=False)
    daily_digest = digest()

    summary = dispatch_daily_digest(session, daily_digest, channels=[channel])

    assert summary.deliveries_failed == 1
    assert daily_digest.status == DigestStatus.FAILED
    assert session.added[0].status == DeliveryStatus.FAILED


def test_dispatch_daily_digest_is_idempotent_for_existing_delivery() -> None:
    existing = DailyDigestDelivery(
        daily_digest_id=1,
        channel=DeliveryChannel.EMAIL,
        destination_label="user@example.test",
        destination_hash=NotificationDestination(
            label="user@example.test",
            hash_source="email:user@example.test",
        ).destination_hash,
        status=DeliveryStatus.SENT,
        attempt_count=1,
    )
    session = _FakeSession(existing=existing)
    channel = _FakeChannel()

    summary = dispatch_daily_digest(session, digest(), channels=[channel])

    assert summary.deliveries_skipped_existing == 1
    assert session.added == []
    assert channel.send_calls == 0
