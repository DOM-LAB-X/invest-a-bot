from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.models import (
    Alert,
    AlertEnrichment,
    AlertStatus,
    DailyDigest,
    DigestScope,
    DigestStatus,
    EnrichmentItem,
)
from app.db.session import get_db
from app.main import app


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _FakeSession:
    def __init__(self, *, execute_responses=None, get_values=None):
        self.execute_responses = list(execute_responses or [])
        self.get_values = get_values or {}
        self.committed = False

    def execute(self, _statement):
        if not self.execute_responses:
            raise AssertionError("No fake execute response queued")
        return _ScalarResult(self.execute_responses.pop(0))

    def get(self, model, item_id):
        return self.get_values.get((model, item_id))

    def commit(self):
        self.committed = True


def _client_with_session(session: _FakeSession) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def make_alert(**overrides) -> Alert:
    values = {
        "id": 10,
        "profile_id": 1,
        "profile_rule_id": 2,
        "transaction_id": 3,
        "filing_document_id": 4,
        "status": AlertStatus.NEW,
        "score": 87,
        "reasons": {"ticker_match": True, "final_score": 87},
        "matched_at": datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc),
        "filer_name_snapshot": "Hon. Mark Alford",
        "ticker_snapshot": "AAPL",
        "asset_name_snapshot": "Apple Inc. - Common Stock",
        "transaction_type_snapshot": "purchase",
        "amount_min_snapshot": Decimal("1001.00"),
        "amount_max_snapshot": Decimal("15000.00"),
        "transaction_date_snapshot": date(2026, 6, 20),
        "filing_delay_days_snapshot": 1,
        "parser_confidence_snapshot": 0.98,
        "needs_review_snapshot": False,
        "source_url_snapshot": "https://example.test/source.pdf",
    }
    values.update(overrides)
    return Alert(**values)


def test_alerts_dashboard_renders_alerts_and_enrichments() -> None:
    alert = make_alert()
    item = EnrichmentItem(
        id=20,
        source="sec_edgar",
        external_id="0000320193-26-000100",
        ticker="AAPL",
        cik="0000320193",
        event_type="sec_filing",
        form_type="8-K",
        title="AAPL 8-K filed with SEC",
        filed_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        url="https://www.sec.gov/Archives/example",
    )
    enrichment = AlertEnrichment(
        id=30,
        alert_id=alert.id,
        enrichment_item_id=item.id,
        relevance_score=95,
        reason="same ticker AAPL",
        enrichment_item=item,
    )
    session = _FakeSession(execute_responses=[[alert], [enrichment]])
    client = _client_with_session(session)
    try:
        response = client.get("/dashboard/alerts")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert "Hon. Mark Alford" in response.text
    assert "Apple Inc. - Common Stock" in response.text
    assert "AAPL 8-K filed with SEC" in response.text
    assert "same ticker AAPL" in response.text


def test_alerts_dashboard_mark_read_and_dismiss_mutate_status() -> None:
    alert = make_alert()
    session = _FakeSession(get_values={(Alert, alert.id): alert})
    client = _client_with_session(session)
    try:
        read_response = client.post(f"/dashboard/alerts/{alert.id}/read", follow_redirects=False)
        assert read_response.status_code == 303
        assert alert.status == AlertStatus.READ
        assert session.committed is True

        session.committed = False
        dismiss_response = client.post(f"/dashboard/alerts/{alert.id}/dismiss", follow_redirects=False)
        assert dismiss_response.status_code == 303
        assert alert.status == AlertStatus.DISMISSED
        assert session.committed is True
    finally:
        _clear_overrides()


def make_digest(**overrides) -> DailyDigest:
    values = {
        "id": 40,
        "digest_date": date(2026, 6, 21),
        "timezone": "America/New_York",
        "scope": DigestScope.PROFILE,
        "profile_id": 1,
        "status": DigestStatus.GENERATED,
        "payload": {
            "scope": "profile",
            "digest_date": "2026-06-21",
            "timezone": "America/New_York",
            "profile": {"id": 1, "name": "Mark Alford watch"},
            "alert_count": 1,
            "ticker_summaries": {
                "AAPL": {
                    "count": 1,
                    "purchase_count": 1,
                    "sale_count": 0,
                    "purchase_amount_min_total": "1001.00",
                    "sale_amount_min_total": "0",
                    "net_purchase_amount_min": "1001.00",
                }
            },
            "filing_delay_stats": {"min": 1, "max": 1, "avg": 1.0},
            "top_alerts": [
                {
                    "alert_id": 10,
                    "ticker": "AAPL",
                    "asset_name": "Apple Inc.",
                    "filer_name": "Hon. Mark Alford",
                    "transaction_type": "purchase",
                    "amount_min": "1001.00",
                    "amount_max": "15000.00",
                    "score": 87,
                    "source_url": "https://example.test/source.pdf",
                }
            ],
        },
        "generated_at": datetime(2026, 6, 21, 20, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return DailyDigest(**values)


def test_daily_digests_dashboard_renders_list() -> None:
    digest = make_digest()
    session = _FakeSession(execute_responses=[[digest]])
    client = _client_with_session(session)
    try:
        response = client.get("/dashboard/daily-digests")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert "2026-06-21" in response.text
    assert "1 matched alert" in response.text
    assert f"/dashboard/daily-digests/{digest.id}" in response.text


def test_daily_digest_detail_renders_structured_payload() -> None:
    digest = make_digest()
    session = _FakeSession(get_values={(DailyDigest, digest.id): digest})
    client = _client_with_session(session)
    try:
        response = client.get(f"/dashboard/daily-digests/{digest.id}")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert "Profile Summary" in response.text
    assert "Mark Alford watch" in response.text
    assert "Ticker Summaries" in response.text
    assert "Top Alerts" in response.text
    assert "Raw Payload" in response.text

