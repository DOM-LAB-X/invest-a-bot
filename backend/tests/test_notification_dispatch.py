from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Alert, DeliveryChannel, DeliveryStatus, NotificationDelivery
from app.notifications.base import NotificationChannel, NotificationDestination, NotificationResult
from app.services.notification_dispatch import dispatch_alert


def alert() -> Alert:
    return Alert(
        id=1,
        profile_id=1,
        profile_rule_id=1,
        transaction_id=1,
        filing_document_id=1,
        score=72,
        ticker_snapshot="AMZN",
        asset_name_snapshot="Amazon.com, Inc.",
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

    def destinations(self) -> list[NotificationDestination]:
        if not self.configured:
            return []
        return [NotificationDestination(label="user@example.test", hash_source="email:user@example.test")]

    def send(self, alert: Alert, destination: NotificationDestination) -> NotificationResult:
        self.send_calls += 1
        return NotificationResult(
            sent=self.sent,
            request_payload={"alert_id": alert.id},
            response_status_code=200 if self.sent else 500,
            response_body="ok" if self.sent else "bad",
            provider_message_id="provider-1" if self.sent else None,
            error=None if self.sent else "bad",
        )


def test_dispatch_alert_skips_unconfigured_channel_without_db_row() -> None:
    session = _FakeSession()
    channel = _FakeChannel(configured=False)

    summary = dispatch_alert(session, alert(), [channel])

    assert summary.channels_skipped_unconfigured == 1
    assert summary.deliveries_sent == 0
    assert session.added == []
    assert channel.send_calls == 0


def test_dispatch_alert_creates_sent_delivery() -> None:
    session = _FakeSession()
    channel = _FakeChannel(sent=True)

    summary = dispatch_alert(session, alert(), [channel])

    assert summary.deliveries_sent == 1
    assert summary.deliveries_failed == 0
    assert len(session.added) == 1
    delivery = session.added[0]
    assert isinstance(delivery, NotificationDelivery)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.attempt_count == 1
    assert delivery.provider == "fake"
    assert delivery.provider_message_id == "provider-1"
    assert delivery.request_payload == {"alert_id": 1}


def test_dispatch_alert_records_failed_delivery() -> None:
    session = _FakeSession()
    channel = _FakeChannel(sent=False)

    summary = dispatch_alert(session, alert(), [channel])

    assert summary.deliveries_sent == 0
    assert summary.deliveries_failed == 1
    delivery = session.added[0]
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.error == "bad"
    assert delivery.response_status_code == 500


def test_dispatch_alert_is_idempotent_for_existing_delivery() -> None:
    existing = NotificationDelivery(
        alert_id=1,
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

    summary = dispatch_alert(session, alert(), [channel])

    assert summary.deliveries_skipped_existing == 1
    assert summary.deliveries_sent == 0
    assert session.added == []
    assert channel.send_calls == 0
