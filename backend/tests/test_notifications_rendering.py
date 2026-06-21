from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Alert
from app.notifications.rendering import DISCLAIMER, render_alert_text, render_email_subject, render_slack_payload


def alert() -> Alert:
    return Alert(
        id=1,
        profile_id=1,
        profile_rule_id=1,
        transaction_id=1,
        filing_document_id=1,
        score=72,
        matched_at=datetime.now(timezone.utc),
        filer_name_snapshot="Hon. Mark Alford",
        ticker_snapshot="AMZN",
        asset_name_snapshot="Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        transaction_type_snapshot="sale",
        amount_min_snapshot=Decimal("1001.00"),
        amount_max_snapshot=Decimal("15000.00"),
        transaction_date_snapshot=datetime(2026, 3, 16, tzinfo=timezone.utc).date(),
        filing_delay_days_snapshot=15,
        source_url_snapshot="https://example.test/source.pdf",
        reasons={"components": [{"label": "exact_ticker_match", "points": 10}]},
    )


def test_render_alert_text_includes_required_disclaimer_and_source() -> None:
    text = render_alert_text(alert())

    assert DISCLAIMER == "Public disclosure alert — not investment advice."
    assert DISCLAIMER in text
    assert "Hon. Mark Alford" in text
    assert "AMZN" in text
    assert "Source: https://example.test/source.pdf" in text


def test_render_email_subject_and_slack_payload() -> None:
    subject = render_email_subject(alert())
    payload = render_slack_payload(alert())

    assert subject == "Congress trade alert: AMZN sale by Hon. Mark Alford"
    assert payload["text"].startswith(subject)
    assert DISCLAIMER in payload["text"]
