from __future__ import annotations

from decimal import Decimal

from app.db.models import FilingDocument, ParserStatus, Transaction
from app.ingestion.pdf_parse import ParsedTransaction, ParseResult, persist_parse_result


class FakeSession:
    def __init__(self) -> None:
        self.executed = []
        self.added = []

    def execute(self, statement):
        self.executed.append(statement)

    def add(self, value):
        self.added.append(value)


def test_persist_parse_result_replaces_transactions_and_updates_document() -> None:
    document = FilingDocument(
        id=7,
        source="house_clerk",
        source_document_id="20024843",
        source_url="https://example.test/20024843.pdf",
        filer_name="Hon. Jane Doe",
        chamber="house",
        parser_status=ParserStatus.PENDING,
    )
    parsed = ParsedTransaction(
        source_transaction_id="stable-row-id",
        row_index=0,
        page_number=3,
        owner="SP",
        asset_name_raw="Apple Inc. (AAPL)",
        ticker="AAPL",
        security_type=None,
        transaction_type="purchase",
        transaction_date=None,
        notification_date=None,
        amount_range_raw="$1,001 - $15,000",
        amount_min=Decimal("1001.00"),
        amount_max=Decimal("15000.00"),
        capital_gains_over_200=False,
        description_raw=None,
        raw_extracted_fields={"Asset": "Apple Inc. (AAPL)"},
        parser_confidence=0.95,
        parser_notes=None,
        needs_review=False,
    )
    session = FakeSession()

    persist_parse_result(
        session,
        document,
        ParseResult(transactions=[parsed], confidence=0.95, status=ParserStatus.PARSED),
    )

    assert len(session.executed) == 1
    assert len(session.added) == 1
    transaction = session.added[0]
    assert isinstance(transaction, Transaction)
    assert transaction.filing_document_id == 7
    assert transaction.source == "house_clerk"
    assert transaction.source_transaction_id == "stable-row-id"
    assert transaction.asset_name_raw == "Apple Inc. (AAPL)"
    assert transaction.amount_min == Decimal("1001.00")
    assert transaction.needs_review is False

    assert document.parser_status == ParserStatus.PARSED
    assert document.parser_confidence == 0.95
    assert document.transaction_count == 1
    assert document.parser_error is None
