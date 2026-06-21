from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import Alert, FilingDocument, Profile, ProfileRule, Transaction
from app.profiles.matching import MatchResult
from app.services.alerting import build_alert, create_alert_for_match


def make_match() -> MatchResult:
    profile = Profile(id=1, name="Mark Alford watch", is_active=True)
    rule = ProfileRule(id=2, profile_id=1, filer_names=["Mark Alford"], min_parser_confidence=0.85)
    filing = FilingDocument(
        id=3,
        source="house_clerk",
        source_document_id="20034201",
        source_url="https://example.test/20034201.pdf",
        filer_name="Hon. Mark Alford",
        chamber="house",
        filed_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    transaction = Transaction(
        id=4,
        filing_document_id=3,
        source="house_clerk",
        source_transaction_id="row-1",
        asset_name_raw="Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        ticker="AMZN",
        transaction_type="sale",
        transaction_date=datetime(2026, 3, 16, tzinfo=timezone.utc).date(),
        amount_min=Decimal("1001.00"),
        amount_max=Decimal("15000.00"),
        parser_confidence=1.0,
        needs_review=False,
    )
    return MatchResult(
        profile=profile,
        rule=rule,
        transaction=transaction,
        filing_document=filing,
        filing_delay_days=15,
        matched_conditions={"ticker": "AMZN"},
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
        self.flushed = False

    def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True


def test_build_alert_populates_snapshot_fields() -> None:
    alert = build_alert(make_match())

    assert alert.profile_id == 1
    assert alert.profile_rule_id == 2
    assert alert.transaction_id == 4
    assert alert.filing_document_id == 3
    assert alert.filer_name_snapshot == "Hon. Mark Alford"
    assert alert.ticker_snapshot == "AMZN"
    assert alert.asset_name_snapshot.startswith("Amazon.com")
    assert alert.filing_delay_days_snapshot == 15
    assert alert.source_url_snapshot == "https://example.test/20034201.pdf"
    assert alert.score > 0
    assert alert.reasons["final_score"] == alert.score


def test_create_alert_for_match_is_idempotent_when_alert_exists() -> None:
    existing = Alert(id=99, profile_id=1, profile_rule_id=2, transaction_id=4, filing_document_id=3, score=50)
    session = _FakeSession(existing=existing)

    alert = create_alert_for_match(session, make_match())

    assert alert is None
    assert session.added == []
    assert session.flushed is False


def test_create_alert_for_match_adds_new_alert() -> None:
    session = _FakeSession(existing=None)

    alert = create_alert_for_match(session, make_match())

    assert alert is not None
    assert session.added == [alert]
    assert session.flushed is True
