from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.db.models import Alert, DailyDigest, DigestScope, DigestStatus, FilingDocument, Profile, Transaction
from app.services.daily_digest import (
    build_cross_profile_digest_payload,
    build_profile_digest_payload,
    create_or_update_digest,
    digest_window_for_date,
    rank_transaction_importance,
)


def profile() -> Profile:
    return Profile(id=1, name="Mark Alford watch", is_active=True)


def alert(**overrides) -> Alert:
    values = {
        "id": 10,
        "profile_id": 1,
        "profile_rule_id": 1,
        "transaction_id": 1,
        "filing_document_id": 1,
        "score": 72,
        "matched_at": datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc),
        "filer_name_snapshot": "Hon. Mark Alford",
        "ticker_snapshot": "AMZN",
        "asset_name_snapshot": "Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        "transaction_type_snapshot": "sale",
        "amount_min_snapshot": Decimal("1001.00"),
        "amount_max_snapshot": Decimal("15000.00"),
        "transaction_date_snapshot": date(2026, 3, 16),
        "filing_delay_days_snapshot": 15,
        "source_url_snapshot": "https://example.test/source.pdf",
    }
    values.update(overrides)
    return Alert(**values)


def transaction(**overrides) -> Transaction:
    values = {
        "id": 20,
        "filing_document_id": 30,
        "source": "house_clerk",
        "source_transaction_id": "row-1",
        "asset_name_raw": "Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        "ticker": "AMZN",
        "transaction_type": "purchase",
        "transaction_date": date(2026, 6, 20),
        "amount_min": Decimal("1000000.00"),
        "amount_max": Decimal("5000000.00"),
        "parser_confidence": 1.0,
        "needs_review": False,
        "created_at": datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Transaction(**values)


def filing_document(**overrides) -> FilingDocument:
    values = {
        "id": 30,
        "source": "house_clerk",
        "source_document_id": "20034201",
        "source_url": "https://example.test/source.pdf",
        "filer_name": "Hon. Mark Alford",
        "chamber": "house",
        "filed_at": datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return FilingDocument(**values)


def test_digest_window_for_date_uses_calendar_day_in_timezone() -> None:
    window = digest_window_for_date(date(2026, 6, 21), "America/New_York")

    assert window.start_utc == datetime(2026, 6, 21, 4, 0, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 6, 22, 4, 0, tzinfo=timezone.utc)


def test_build_profile_digest_payload_summarizes_alerts_by_ticker() -> None:
    window = digest_window_for_date(date(2026, 6, 21), "America/New_York")
    payload = build_profile_digest_payload(
        profile=profile(),
        alerts=[alert(), alert(id=11, ticker_snapshot="AAPL", score=80)],
        window=window,
        top_n=1,
    )

    assert payload["scope"] == "profile"
    assert payload["alert_count"] == 2
    assert payload["ticker_summaries"]["AMZN"]["sale_count"] == 1
    assert payload["ticker_summaries"]["AAPL"]["sale_amount_min_total"] == "1001.00"
    assert payload["top_alerts"][0]["score"] == 80


def test_build_cross_profile_digest_payload_ranks_top_disclosures() -> None:
    window = digest_window_for_date(date(2026, 6, 21), "America/New_York")
    high = transaction(id=20, ticker="AMZN", amount_min=Decimal("1000000.00"), transaction_type="purchase")
    low = transaction(id=21, ticker=None, amount_min=Decimal("1001.00"), transaction_type="sale")

    payload = build_cross_profile_digest_payload(
        transactions=[(low, filing_document()), (high, filing_document())],
        window=window,
        top_n=1,
    )

    assert payload["scope"] == "cross_profile"
    assert payload["transaction_count"] == 2
    assert payload["ticker_summaries"]["AMZN"]["purchase_count"] == 1
    assert payload["top_disclosures"][0]["transaction_id"] == 20
    assert payload["top_disclosures"][0]["ticker"] == "AMZN"


def test_rank_transaction_importance_rewards_ticker_purchase_amount_and_freshness() -> None:
    score = rank_transaction_importance(transaction(), filing_document())

    assert score == 90


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flushed = False

    def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True


def test_create_or_update_digest_is_idempotent_without_force() -> None:
    existing = DailyDigest(
        id=1,
        digest_date=date(2026, 6, 21),
        timezone="America/New_York",
        scope=DigestScope.CROSS_PROFILE,
        status=DigestStatus.GENERATED,
        payload={"existing": True},
    )
    session = _FakeSession(existing=existing)

    digest = create_or_update_digest(
        session,
        digest_date=date(2026, 6, 21),
        timezone_name="America/New_York",
        scope=DigestScope.CROSS_PROFILE,
        profile_id=None,
        payload={"existing": False},
    )

    assert digest is existing
    assert digest.payload == {"existing": True}
    assert session.added == []


def test_create_or_update_digest_creates_new_digest() -> None:
    session = _FakeSession(existing=None)

    digest = create_or_update_digest(
        session,
        digest_date=date(2026, 6, 21),
        timezone_name="America/New_York",
        scope=DigestScope.PROFILE,
        profile_id=1,
        payload={"profile": {"id": 1}},
    )

    assert digest.scope == DigestScope.PROFILE
    assert digest.profile_id == 1
    assert digest.payload == {"profile": {"id": 1}}
    assert session.added == [digest]
    assert session.flushed is True
