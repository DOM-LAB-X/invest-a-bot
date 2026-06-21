from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import FilingDocument, Profile, ProfileRule, Transaction
from app.profiles.matching import (
    TransactionContext,
    match_transaction_context,
    normalize_filer_name,
    rule_matches_transaction,
)


def filing_document(**overrides) -> FilingDocument:
    values = {
        "id": 10,
        "source": "house_clerk",
        "source_document_id": "20034201",
        "source_url": "https://example.test/20034201.pdf",
        "filer_name": "Hon. Mark Alford",
        "chamber": "house",
        "filed_at": datetime(2026, 3, 31, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return FilingDocument(**values)


def transaction(**overrides) -> Transaction:
    values = {
        "id": 20,
        "filing_document_id": 10,
        "source": "house_clerk",
        "source_transaction_id": "row-1",
        "asset_name_raw": "Amazon.com, Inc. - Common Stock (AMZN) [ST]",
        "ticker": "AMZN",
        "transaction_type": "sale",
        "transaction_date": datetime(2026, 3, 16, tzinfo=timezone.utc).date(),
        "amount_min": Decimal("1001.00"),
        "amount_max": Decimal("15000.00"),
        "parser_confidence": 1.0,
        "needs_review": False,
    }
    values.update(overrides)
    return Transaction(**values)


def profile_rule(**overrides) -> ProfileRule:
    values = {
        "id": 30,
        "profile_id": 40,
        "is_active": True,
        "filer_names": ["Mark Alford"],
        "tickers": ["AMZN", "AAPL"],
        "min_amount": Decimal("1000.00"),
        "min_parser_confidence": 0.85,
        "include_needs_review": False,
    }
    values.update(overrides)
    return ProfileRule(**values)


def profile(**overrides) -> Profile:
    values = {"id": 40, "name": "Mark Alford watch", "is_active": True}
    values.update(overrides)
    return Profile(**values)


def test_normalize_filer_name_strips_common_prefixes_and_casefolds() -> None:
    assert normalize_filer_name("Hon. Mark Alford") == "mark alford"
    assert normalize_filer_name("  Dr.   Jane   Doe  ") == "jane doe"
    assert normalize_filer_name("mark alford") == "mark alford"


def test_rule_matches_house_filer_name_primary_path() -> None:
    matched, details = rule_matches_transaction(
        profile_rule(max_filing_delay_days=30),
        transaction(),
        filing_document(),
    )

    assert matched is True
    assert details["filer_name"] == "mark alford"
    assert details["ticker"] == "AMZN"
    assert details["filing_delay_days"] == 15


def test_rule_rejects_low_parser_confidence_by_default() -> None:
    matched, details = rule_matches_transaction(
        profile_rule(min_parser_confidence=0.85),
        transaction(parser_confidence=0.5),
        filing_document(),
    )

    assert matched is False
    assert details["parser_confidence"] == 0.5


def test_rule_rejects_needs_review_unless_explicitly_allowed() -> None:
    reviewed_transaction = transaction(needs_review=True)

    rejected, _ = rule_matches_transaction(profile_rule(include_needs_review=False), reviewed_transaction, filing_document())
    accepted, _ = rule_matches_transaction(profile_rule(include_needs_review=True), reviewed_transaction, filing_document())

    assert rejected is False
    assert accepted is True


def test_rule_rejects_sector_rules_until_enrichment_exists() -> None:
    matched, details = rule_matches_transaction(profile_rule(sectors=["technology"]), transaction(), filing_document())

    assert matched is False
    assert details["sectors"] == "sector matching unavailable until enrichment"


def test_match_transaction_context_returns_profile_rule_matches() -> None:
    active_profile = profile()
    rule = profile_rule()
    context = TransactionContext(transaction=transaction(), filing_document=filing_document())

    matches = match_transaction_context(context, [(active_profile, rule)])

    assert len(matches) == 1
    assert matches[0].profile is active_profile
    assert matches[0].rule is rule
    assert matches[0].filing_delay_days == 15
