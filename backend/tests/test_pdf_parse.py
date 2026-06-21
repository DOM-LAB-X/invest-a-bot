from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.db.models import FilingDocument, ParserStatus
from app.ingestion.pdf_parse import (
    RawExtractedRow,
    ParseResult,
    extract_ticker,
    normalize_raw_row,
    parse_amount_range,
    parse_filing_document,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def filing_document() -> FilingDocument:
    return FilingDocument(
        id=42,
        source="house_clerk",
        source_document_id="20024843",
        source_url="https://example.test/20024843.pdf",
        filer_name="Hon. Jane Doe",
        chamber="house",
        storage_path="/tmp/example.pdf",
    )


def test_parse_amount_range_handles_ranges_and_over_values() -> None:
    assert parse_amount_range("$1,001 - $15,000") == (Decimal("1001.00"), Decimal("15000.00"))
    assert parse_amount_range("Over $50,000,000") == (Decimal("50000000.00"), None)
    assert parse_amount_range(None) == (None, None)


def test_extract_ticker_prefers_explicit_ticker_then_parenthetical() -> None:
    assert extract_ticker("Apple Inc. (AAPL)", None) == "AAPL"
    assert extract_ticker("Apple Inc. (AAPL)", "$MSFT") == "MSFT"
    assert extract_ticker("DIA - State Street SPDR Dow Jones Indust Avg ETF Trust NYSEARCA: DIA [OT]") == "DIA"
    assert extract_ticker("Invesco QQQ [OT]") == "QQQ"
    assert extract_ticker("US Treasury Note", None) is None


def test_normalize_raw_row_builds_transaction_with_confidence() -> None:
    row = RawExtractedRow(
        row_index=0,
        page_number=2,
        fields={
            "Owner": "SP",
            "Asset": "NVIDIA Corporation (NVDA)",
            "Transaction Type": "Purchase",
            "Transaction Date": "06/20/2026",
            "Notification Date": "06/21/2026",
            "Amount": "$1,001 - $15,000",
            "Capital Gains > $200?": "No",
        },
    )

    parsed = normalize_raw_row(filing_document(), row)

    assert parsed is not None
    assert parsed.source_transaction_id
    assert parsed.asset_name_raw == "NVIDIA Corporation (NVDA)"
    assert parsed.ticker == "NVDA"
    assert parsed.transaction_type == "purchase"
    assert parsed.amount_min == Decimal("1001.00")
    assert parsed.amount_max == Decimal("15000.00")
    assert parsed.capital_gains_over_200 is False
    assert parsed.parser_confidence >= 0.9
    assert parsed.needs_review is False
    assert parsed.raw_extracted_fields["_page_number"] == 2


def test_normalize_raw_row_recovers_single_column_trade_blob() -> None:
    row = RawExtractedRow(
        row_index=0,
        page_number=1,
        fields={
            "ID": (
                "Amazon.com, Inc. - Common Stock S (partial) 03/16/2026 "
                "03/16/2026 $1,001 - $15,000\n"
                "(AMZN) [ST]\n"
                "Filing Status: New\n"
                "Source Of: Putnam Investments"
            ),
            "Asset": None,
            "Transaction Type": None,
            "Date": None,
            "Notification Date": None,
            "Amount": None,
        },
    )

    parsed = normalize_raw_row(filing_document(), row)

    assert parsed is not None
    assert parsed.asset_name_raw == "Amazon.com, Inc. - Common Stock (AMZN) [ST]"
    assert parsed.ticker == "AMZN"
    assert parsed.transaction_type == "sale"
    assert parsed.transaction_date.isoformat() == "2026-03-16"
    assert parsed.amount_min == Decimal("1001.00")
    assert parsed.needs_review is False
    assert "_recovered_from_blob" in parsed.raw_extracted_fields


def test_normalize_raw_row_excludes_metadata_only_rows() -> None:
    row = RawExtractedRow(
        row_index=1,
        page_number=1,
        fields={
            "Asset": (
                "Filing Status: New\n"
                "Source Of: Putnam Investments\n"
                "Description: The full transaction included the following sales: AMZN shares sold."
            ),
            "Transaction Type": None,
            "Date": None,
            "Amount": None,
        },
    )

    assert normalize_raw_row(filing_document(), row) is None


def test_normalize_raw_row_flags_low_confidence_rows() -> None:
    row = RawExtractedRow(
        row_index=0,
        page_number=1,
        fields={"Asset": "Ambiguous Asset LLC", "Amount": "Unknown"},
    )

    parsed = normalize_raw_row(filing_document(), row)

    assert parsed is not None
    assert parsed.needs_review is True
    assert "missing transaction date" in (parsed.parser_notes or "")
    assert "missing transaction type" in (parsed.parser_notes or "")


def test_parse_filing_document_fails_without_storage_path() -> None:
    document = filing_document()
    document.storage_path = None

    result = parse_filing_document(document)

    assert result.status == ParserStatus.FAILED
    assert result.error == "missing storage_path"


def test_parse_real_house_ptr_fixture_captures_trade_rows_without_metadata_junk() -> None:
    document = filing_document()
    document.source_document_id = "20034201"
    document.storage_path = str(FIXTURES_DIR / "house_ptr_20034201.pdf")

    result = parse_filing_document(document)

    tickers = [transaction.ticker for transaction in result.transactions]
    assert result.status == ParserStatus.PARSED
    assert len(result.transactions) == 9
    assert tickers == ["AMZN", "AAPL", "T", "BRK.B", "DIA", "QQQ", "PYPL", "SPYB", "SPYB"]
    assert all(not transaction.needs_review for transaction in result.transactions)
    assert all("Putnam Investments" not in transaction.asset_name_raw for transaction in result.transactions)


def test_parse_result_defaults_to_failed() -> None:
    result = ParseResult()

    assert result.status == ParserStatus.FAILED
    assert result.transactions == []
