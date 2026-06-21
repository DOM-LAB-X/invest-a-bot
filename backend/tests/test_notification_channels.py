from __future__ import annotations

import httpx

from app.core.config import Settings
from app.db.models import Alert
from app.notifications.email import RESEND_EMAILS_URL, ResendEmailChannel
from app.notifications.slack import SlackWebhookChannel


def alert() -> Alert:
    return Alert(
        id=1,
        profile_id=1,
        profile_rule_id=1,
        transaction_id=1,
        filing_document_id=1,
        score=72,
        filer_name_snapshot="Hon. Mark Alford",
        ticker_snapshot="AMZN",
        asset_name_snapshot="Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        transaction_type_snapshot="sale",
    )


def test_resend_email_channel_skips_when_unconfigured() -> None:
    channel = ResendEmailChannel(config=Settings(notifications_enabled=True))

    assert channel.destinations() == []


def test_resend_email_channel_posts_expected_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email_123"})

    config = Settings(
        notifications_enabled=True,
        resend_api_key="test-key",
        notification_from_email="alerts@example.test",
        notification_to_emails=["user@example.test"],
    )
    channel = ResendEmailChannel(config=config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    destination = channel.destinations()[0]

    result = channel.send(alert(), destination)

    assert result.sent is True
    assert result.provider_message_id == "email_123"
    assert requests[0].url == RESEND_EMAILS_URL
    assert requests[0].headers["authorization"] == "Bearer test-key"
    payload = result.request_payload
    assert payload is not None
    assert payload["from"] == "alerts@example.test"
    assert payload["to"] == ["user@example.test"]
    assert "Public disclosure alert — not investment advice." in payload["text"]


def test_slack_channel_skips_when_unconfigured() -> None:
    channel = SlackWebhookChannel(config=Settings(notifications_enabled=True))

    assert channel.destinations() == []


def test_slack_channel_posts_expected_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    config = Settings(notifications_enabled=True, slack_webhook_url="https://hooks.slack.test/abc")
    channel = SlackWebhookChannel(config=config, client=httpx.Client(transport=httpx.MockTransport(handler)))
    destination = channel.destinations()[0]

    result = channel.send(alert(), destination)

    assert result.sent is True
    assert requests[0].url == "https://hooks.slack.test/abc"
    payload = result.request_payload
    assert payload is not None
    assert "AMZN" in payload["text"]
    assert "Public disclosure alert — not investment advice." in payload["text"]
