from __future__ import annotations

from datetime import date

from app.db.models import DailyDigest, DigestScope
from app.notifications.digest_rendering import digest_subject, render_daily_digest_message, render_daily_digest_text
from app.notifications.rendering import DISCLAIMER


def test_render_profile_digest_message_includes_disclaimer() -> None:
    digest = DailyDigest(
        id=1,
        digest_date=date(2026, 6, 21),
        timezone="America/New_York",
        scope=DigestScope.PROFILE,
        profile_id=1,
        payload={
            "profile": {"id": 1, "name": "Mark Alford watch"},
            "alert_count": 2,
            "ticker_summaries": {"AMZN": {"count": 1, "purchase_amount_min_total": "0", "sale_amount_min_total": "1001.00", "net_purchase_amount_min": "-1001.00"}},
            "top_alerts": [{"ticker": "AMZN", "transaction_type": "sale", "filer_name": "Hon. Mark Alford", "score": 72}],
        },
    )

    text = render_daily_digest_text(digest)
    message = render_daily_digest_message(digest)

    assert digest_subject(digest) == "Daily congressional trade digest: Mark Alford watch (2026-06-21)"
    assert message.subject == digest_subject(digest)
    assert "Alerts matched: 2" in text
    assert "AMZN" in text
    assert DISCLAIMER in text


def test_render_cross_profile_digest_message() -> None:
    digest = DailyDigest(
        id=1,
        digest_date=date(2026, 6, 21),
        timezone="America/New_York",
        scope=DigestScope.CROSS_PROFILE,
        profile_id=None,
        payload={"transaction_count": 0, "ticker_summaries": {}, "top_disclosures": []},
    )

    message = render_daily_digest_message(digest)

    assert message.subject == "Daily congressional trade digest: all new disclosures (2026-06-21)"
    assert "Clean new transactions: 0" in message.text
    assert DISCLAIMER in message.text
