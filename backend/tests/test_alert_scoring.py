from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import FilingDocument, Profile, ProfileRule, Transaction
from app.profiles.matching import MatchResult
from app.services.scoring import score_match


def make_match(**transaction_overrides) -> MatchResult:
    profile = Profile(id=1, name="Mark Alford watch", is_active=True)
    rule = ProfileRule(
        id=2,
        profile_id=1,
        filer_names=["Mark Alford"],
        tickers=["AMZN"],
        min_parser_confidence=0.85,
        include_needs_review=False,
    )
    filing = FilingDocument(
        id=3,
        source="house_clerk",
        source_document_id="20034201",
        source_url="https://example.test/20034201.pdf",
        filer_name="Hon. Mark Alford",
        chamber="house",
        filed_at=datetime(2026, 3, 18, tzinfo=timezone.utc),
    )
    values = {
        "id": 4,
        "filing_document_id": 3,
        "source": "house_clerk",
        "source_transaction_id": "row-1",
        "asset_name_raw": "Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        "ticker": "AMZN",
        "transaction_type": "purchase",
        "transaction_date": datetime(2026, 3, 16, tzinfo=timezone.utc).date(),
        "amount_min": Decimal("1000000.00"),
        "amount_max": Decimal("5000000.00"),
        "parser_confidence": 1.0,
        "needs_review": False,
    }
    values.update(transaction_overrides)
    transaction = Transaction(**values)
    return MatchResult(
        profile=profile,
        rule=rule,
        transaction=transaction,
        filing_document=filing,
        filing_delay_days=2,
        matched_conditions={"ticker": "AMZN", "filer_name": "mark alford"},
    )


def test_score_match_applies_deterministic_components_and_clamps_to_100() -> None:
    result = score_match(make_match())

    assert result.score == 100
    labels = [component["label"] for component in result.reasons["components"]]
    assert "base_rule_match" in labels
    assert "exact_ticker_match" in labels
    assert "exact_filer_match" in labels
    assert "purchase" in labels
    assert "amount_at_least_1000000" in labels
    assert "fresh_disclosure_delay_lte_3_days" in labels


def test_score_caps_needs_review_alerts() -> None:
    result = score_match(make_match(needs_review=True))

    assert result.score == 50
    assert result.reasons["caps"][0]["label"] == "needs_review_cap"


def test_score_caps_missing_date_or_amount() -> None:
    result = score_match(make_match(transaction_date=None, amount_min=None))

    assert result.score == 70
    assert any(cap["label"] == "missing_date_or_amount_cap" for cap in result.reasons["caps"])


def test_score_penalizes_low_parser_confidence() -> None:
    result = score_match(make_match(parser_confidence=0.9, amount_min=Decimal("1001.00")))

    labels = [component["label"] for component in result.reasons["components"]]
    assert "parser_confidence_below_0_95" in labels
