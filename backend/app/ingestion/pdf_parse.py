from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import FilingDocument, ParserStatus, Transaction
from app.db.session import SessionLocal

PARSER_VERSION = "house-pdf-v0.1"
AMOUNT_OVER_RE = re.compile(r"over\s*\$?\s*([\d,]+)", re.IGNORECASE)
AMOUNT_RE = re.compile(r"\$?\s*([\d,]+)(?:\.\d+)?")
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9})\)")
LEADING_TICKER_RE = re.compile(r"^([A-Z][A-Z0-9.\-]{0,9})\s+-\s+")
OT_TICKER_RE = re.compile(r"\b([A-Z][A-Z0-9.\-]{1,9})\s+\[OT\]")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
TRADE_BLOB_RE = re.compile(
    r"(?P<asset>.+?)\s+"
    r"(?P<transaction_type>[PS]\s*\(partial\)|[PS]|purchase|sale(?:\s*\(partial\))?)\s+"
    r"(?P<transaction_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<notification_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|Over\s+\$[\d,]+|\$[\d,]+)"
    r"(?P<trailing>.*)",
    re.IGNORECASE | re.DOTALL,
)
TRADE_LINE_RE = re.compile(
    r"^(?P<asset>.+?)\s+"
    r"(?P<transaction_type>[PS]\s*\(partial\)|[PS]|purchase|sale(?:\s*\(partial\))?)\s+"
    r"(?P<transaction_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<notification_date>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>\$[\d,]+\s*-\s*\$[\d,]+|Over\s+\$[\d,]+|\$[\d,]+)$",
    re.IGNORECASE,
)
KNOWN_HEADER_WORDS = {"owner", "asset", "transaction", "date", "amount", "notification", "ticker"}
METADATA_LABEL_RE = re.compile(
    r"^(?:"
    r"filing\s+status|source\s+of|description|location|"
    r"f\s*s|s\s*o|d|l"
    r")\s*:",
    re.IGNORECASE,
)
METADATA_HINT_RE = re.compile(
    r"(?:putnam investments|full transaction included|filing status|source of|digitally signed)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RawExtractedRow:
    row_index: int
    page_number: int | None
    fields: dict[str, Any]


@dataclass(frozen=True)
class ParsedTransaction:
    source_transaction_id: str
    row_index: int
    page_number: int | None
    owner: str | None
    asset_name_raw: str
    ticker: str | None
    security_type: str | None
    transaction_type: str | None
    transaction_date: date | None
    notification_date: date | None
    amount_range_raw: str | None
    amount_min: Decimal | None
    amount_max: Decimal | None
    capital_gains_over_200: bool | None
    description_raw: str | None
    raw_extracted_fields: dict[str, Any]
    parser_confidence: float
    parser_notes: str | None
    needs_review: bool


@dataclass
class ParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    confidence: float = 0.0
    status: ParserStatus = ParserStatus.FAILED
    error: str | None = None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    value = CONTROL_CHARS_RE.sub("", str(value))
    normalized = " ".join(value.replace("\n", " ").split())
    return normalized or None


def clean_multiline(value: Any) -> str | None:
    if value is None:
        return None
    value = CONTROL_CHARS_RE.sub("", str(value))
    lines = [" ".join(line.split()) for line in value.splitlines()]
    normalized = "\n".join(line for line in lines if line)
    return normalized or None


def first_present(fields: dict[str, Any], *names: str) -> str | None:
    normalized = {str(key).strip().lower(): value for key, value in fields.items() if key is not None}
    for name in names:
        value = normalized.get(name.lower())
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return None


def parse_date(value: str | None) -> date | None:
    value = clean_text(value)
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount_range(value: str | None) -> tuple[Decimal | None, Decimal | None]:
    value = clean_text(value)
    if not value:
        return None, None

    over_match = AMOUNT_OVER_RE.search(value)
    if over_match:
        return _decimal_from_match(over_match.group(1)), None

    amounts = [_decimal_from_match(match) for match in AMOUNT_RE.findall(value)]
    amounts = [amount for amount in amounts if amount is not None]
    if not amounts:
        return None, None
    if len(amounts) == 1:
        return amounts[0], amounts[0]
    return amounts[0], amounts[1]


def _decimal_from_match(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, AttributeError):
        return None


def normalize_transaction_type(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    lowered = value.lower()
    if "purchase" in lowered or lowered in {"p", "buy", "b"} or lowered.startswith("p "):
        return "purchase"
    if "sale" in lowered or lowered in {"s", "sell"} or lowered.startswith("s "):
        return "sale"
    if "exchange" in lowered:
        return "exchange"
    return value


def extract_ticker(asset_name: str | None, explicit_ticker: str | None = None) -> str | None:
    explicit = clean_text(explicit_ticker)
    if explicit:
        return explicit.upper().lstrip("$")
    asset_name = clean_text(asset_name)
    if not asset_name:
        return None
    match = TICKER_RE.search(asset_name)
    if match:
        return match.group(1).upper()
    match = LEADING_TICKER_RE.search(asset_name)
    if match:
        return match.group(1).upper()
    match = OT_TICKER_RE.search(asset_name)
    if match:
        return match.group(1).upper()
    return None


def confidence_for(fields: dict[str, Any], parsed: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.35
    notes: list[str] = []
    if parsed["asset_name_raw"]:
        score += 0.2
    else:
        notes.append("missing asset")
    if parsed["transaction_date"]:
        score += 0.15
    else:
        notes.append("missing transaction date")
    if parsed["transaction_type"]:
        score += 0.1
    else:
        notes.append("missing transaction type")
    if parsed["amount_range_raw"]:
        score += 0.1
    else:
        notes.append("missing amount range")
    if parsed["amount_min"] is not None:
        score += 0.05
    if len(fields) >= 4:
        score += 0.05
    return min(score, 1.0), notes


def stable_transaction_id(filing_document: FilingDocument, row: RawExtractedRow) -> str:
    payload = "|".join(
        [
            filing_document.source,
            filing_document.source_document_id,
            str(row.page_number or ""),
            str(row.row_index),
            repr(sorted(row.fields.items())),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def row_blob(fields: dict[str, Any]) -> str | None:
    values = [clean_multiline(value) for value in fields.values()]
    blob = "\n".join(value for value in values if value)
    return blob or None


def metadata_prefix_index(lines: list[str]) -> int | None:
    for idx, line in enumerate(lines):
        if METADATA_LABEL_RE.match(line):
            return idx
    return None


def strip_metadata_suffix(value: str) -> tuple[str, str | None]:
    lines = [line for line in (clean_multiline(value) or "").splitlines() if line]
    idx = metadata_prefix_index(lines)
    if idx is None:
        return "\n".join(lines), None
    return "\n".join(lines[:idx]), "\n".join(lines[idx:])


def is_metadata_only_fields(fields: dict[str, Any]) -> bool:
    blob = row_blob(fields)
    if not blob:
        return True
    if clean_text(blob) in {"$200?", "Cap. Gains > $200?"}:
        return True

    has_trade_shape = bool(
        parse_date(first_present(fields, "Transaction Date", "Date"))
        or first_present(fields, "Amount", "Amount Range", "Transaction Amount")
        or first_present(fields, "Transaction Type", "Type", "Transaction")
    )
    if has_trade_shape:
        return False

    lines = [line for line in blob.splitlines() if line]
    return bool(lines) and (
        metadata_prefix_index(lines) == 0
        or METADATA_HINT_RE.search(blob) is not None
    )


def recover_fields_from_blob(fields: dict[str, Any]) -> dict[str, Any] | None:
    blob = row_blob(fields)
    if not blob:
        return None

    match = TRADE_BLOB_RE.search(blob)
    if not match:
        return None

    asset_head = clean_text(match.group("asset"))
    trailing_trade_text, metadata_text = strip_metadata_suffix(match.group("trailing"))
    asset_parts = [asset_head, clean_text(trailing_trade_text)]
    asset = clean_text(" ".join(part for part in asset_parts if part))
    if not asset:
        return None

    recovered = dict(fields)
    recovered["Asset"] = asset
    recovered["Transaction Type"] = clean_text(match.group("transaction_type"))
    recovered["Transaction Date"] = clean_text(match.group("transaction_date"))
    recovered["Notification Date"] = clean_text(match.group("notification_date"))
    recovered["Amount"] = clean_text(match.group("amount"))
    recovered["_recovered_from_blob"] = blob
    if metadata_text:
        recovered["_metadata_suffix"] = metadata_text
    return recovered


def auditable_unparsed_transaction(filing_document: FilingDocument, row: RawExtractedRow) -> ParsedTransaction:
    blob = row_blob(row.fields) or "<unparsed row>"
    raw_fields = {str(key): clean_text(value) for key, value in row.fields.items() if clean_text(value)}
    raw_fields["_row_index"] = row.row_index
    raw_fields["_page_number"] = row.page_number
    raw_fields["_parser_version"] = PARSER_VERSION
    raw_fields["_unparsed_blob"] = blob

    return ParsedTransaction(
        source_transaction_id=stable_transaction_id(filing_document, row),
        row_index=row.row_index,
        page_number=row.page_number,
        owner=None,
        asset_name_raw=clean_text(blob) or "<unparsed row>",
        ticker=None,
        security_type=None,
        transaction_type=None,
        transaction_date=None,
        notification_date=None,
        amount_range_raw=None,
        amount_min=None,
        amount_max=None,
        capital_gains_over_200=None,
        description_raw=clean_text(blob),
        raw_extracted_fields=raw_fields,
        parser_confidence=0.1,
        parser_notes="unparsed non-metadata row preserved for review",
        needs_review=True,
    )


def normalize_raw_row(filing_document: FilingDocument, row: RawExtractedRow) -> ParsedTransaction | None:
    fields = row.fields
    has_named_trade_fields = bool(
        first_present(fields, "Asset", "Asset Name", "Asset Name / Description", "Description")
        and first_present(fields, "Transaction Type", "Type", "Transaction")
        and first_present(fields, "Transaction Date", "Date")
        and first_present(fields, "Amount", "Amount Range", "Transaction Amount")
    )
    if not has_named_trade_fields:
        recovered_fields = recover_fields_from_blob(fields)
        if recovered_fields is not None:
            fields = recovered_fields
        elif is_metadata_only_fields(fields):
            return None
    elif is_metadata_only_fields(fields):
        return None

    asset_name = first_present(fields, "Asset", "Asset Name", "Asset Name / Description", "Description")
    if not asset_name:
        return auditable_unparsed_transaction(filing_document, row)

    amount_range = first_present(fields, "Amount", "Amount Range", "Transaction Amount")
    amount_min, amount_max = parse_amount_range(amount_range)
    parsed: dict[str, Any] = {
        "asset_name_raw": asset_name,
        "transaction_date": parse_date(first_present(fields, "Transaction Date", "Date")),
        "transaction_type": normalize_transaction_type(
            first_present(fields, "Transaction Type", "Type", "Transaction")
        ),
        "amount_range_raw": amount_range,
        "amount_min": amount_min,
    }
    confidence, notes = confidence_for(fields, parsed)
    needs_review = confidence < 0.75

    raw_fields = {str(key): clean_text(value) for key, value in fields.items() if clean_text(value)}
    raw_fields["_row_index"] = row.row_index
    raw_fields["_page_number"] = row.page_number
    raw_fields["_parser_version"] = PARSER_VERSION

    return ParsedTransaction(
        source_transaction_id=stable_transaction_id(filing_document, row),
        row_index=row.row_index,
        page_number=row.page_number,
        owner=first_present(fields, "Owner"),
        asset_name_raw=asset_name,
        ticker=extract_ticker(asset_name, first_present(fields, "Ticker")),
        security_type=first_present(fields, "Security Type", "Asset Type"),
        transaction_type=parsed["transaction_type"],
        transaction_date=parsed["transaction_date"],
        notification_date=parse_date(first_present(fields, "Notification Date", "Date Notified")),
        amount_range_raw=amount_range,
        amount_min=amount_min,
        amount_max=amount_max,
        capital_gains_over_200=_parse_capital_gains(first_present(fields, "Capital Gains > $200?", "CG")),
        description_raw=first_present(fields, "Description", "Asset Name / Description"),
        raw_extracted_fields=raw_fields,
        parser_confidence=confidence,
        parser_notes="; ".join(notes) if notes else None,
        needs_review=needs_review,
    )


def _parse_capital_gains(value: str | None) -> bool | None:
    value = clean_text(value)
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"yes", "y", "true"}:
        return True
    if lowered in {"no", "n", "false"}:
        return False
    return None


def row_looks_like_header(fields: dict[str, Any]) -> bool:
    values = {clean_text(value).lower() for value in fields.values() if clean_text(value)}
    return bool(values) and len(values & KNOWN_HEADER_WORDS) >= 2


def extract_table_raw_rows(pdf_path: str | Path) -> list[RawExtractedRow]:
    import pdfplumber

    rows: list[RawExtractedRow] = []
    row_index = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables() or []:
                if not table:
                    continue
                header = [clean_text(cell) or f"column_{idx}" for idx, cell in enumerate(table[0])]
                for raw_row in table[1:]:
                    fields = {
                        header[idx]: raw_row[idx] if idx < len(raw_row) else None
                        for idx in range(len(header))
                    }
                    if not any(clean_text(value) for value in fields.values()) or row_looks_like_header(fields):
                        continue
                    rows.append(RawExtractedRow(row_index=row_index, page_number=page_number, fields=fields))
                    row_index += 1
    return rows


def line_ends_asset_continuation(line: str) -> bool:
    return bool(
        re.search(r"\[[A-Z]{2}\]$", line)
        or TICKER_RE.search(line)
        or OT_TICKER_RE.search(line)
        or line.endswith(":")
    )


def extract_text_trade_rows(pdf_path: str | Path) -> list[RawExtractedRow]:
    import pdfplumber

    rows: list[RawExtractedRow] = []
    row_index = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            lines = [line for line in (clean_multiline(text) or "").splitlines() if line]
            idx = 0
            while idx < len(lines):
                line = lines[idx]
                match = TRADE_LINE_RE.match(line)
                if match is None:
                    idx += 1
                    continue

                continuation: list[str] = []
                next_idx = idx + 1
                while next_idx < len(lines):
                    next_line = lines[next_idx]
                    if TRADE_LINE_RE.match(next_line) or METADATA_LABEL_RE.match(next_line):
                        break
                    if line_ends_asset_continuation(next_line):
                        continuation.append(next_line)
                        next_idx += 1
                        continue
                    break

                asset = clean_text(" ".join([match.group("asset"), *continuation]))
                rows.append(
                    RawExtractedRow(
                        row_index=row_index,
                        page_number=page_number,
                        fields={
                            "Asset": asset,
                            "Transaction Type": match.group("transaction_type"),
                            "Transaction Date": match.group("transaction_date"),
                            "Notification Date": match.group("notification_date"),
                            "Amount": match.group("amount"),
                            "_extraction_method": "text_line",
                        },
                    )
                )
                row_index += 1
                idx = next_idx
    return rows


def extract_raw_rows(pdf_path: str | Path) -> list[RawExtractedRow]:
    text_rows = extract_text_trade_rows(pdf_path)
    if text_rows:
        return text_rows
    return extract_table_raw_rows(pdf_path)


def parse_filing_document(filing_document: FilingDocument) -> ParseResult:
    if not filing_document.storage_path:
        return ParseResult(status=ParserStatus.FAILED, error="missing storage_path")

    try:
        raw_rows = extract_raw_rows(filing_document.storage_path)
        transactions = [
            parsed
            for row in raw_rows
            if (parsed := normalize_raw_row(filing_document, row)) is not None
        ]
    except Exception as exc:
        return ParseResult(status=ParserStatus.FAILED, error=str(exc))

    if not transactions:
        return ParseResult(status=ParserStatus.NEEDS_REVIEW, error="no transactions parsed")

    confidence = sum(txn.parser_confidence for txn in transactions) / len(transactions)
    status = ParserStatus.PARSED if all(not txn.needs_review for txn in transactions) else ParserStatus.PARTIAL
    return ParseResult(transactions=transactions, confidence=confidence, status=status)


def persist_parse_result(session: Session, filing_document: FilingDocument, result: ParseResult) -> None:
    now = datetime.now(timezone.utc)
    filing_document.parser_version = PARSER_VERSION
    filing_document.parser_finished_at = now
    filing_document.parser_error = result.error
    filing_document.parser_status = result.status
    filing_document.parser_confidence = result.confidence
    filing_document.transaction_count = len(result.transactions)

    session.execute(delete(Transaction).where(Transaction.filing_document_id == filing_document.id))
    for parsed in result.transactions:
        session.add(
            Transaction(
                filing_document_id=filing_document.id,
                source=filing_document.source,
                source_transaction_id=parsed.source_transaction_id,
                row_index=parsed.row_index,
                page_number=parsed.page_number,
                owner=parsed.owner,
                asset_name_raw=parsed.asset_name_raw,
                ticker=parsed.ticker,
                security_type=parsed.security_type,
                transaction_type=parsed.transaction_type,
                transaction_date=parsed.transaction_date,
                notification_date=parsed.notification_date,
                amount_range_raw=parsed.amount_range_raw,
                amount_min=parsed.amount_min,
                amount_max=parsed.amount_max,
                capital_gains_over_200=parsed.capital_gains_over_200,
                description_raw=parsed.description_raw,
                raw_extracted_fields=parsed.raw_extracted_fields,
                parser_version=PARSER_VERSION,
                parser_confidence=parsed.parser_confidence,
                parser_notes=parsed.parser_notes,
                needs_review=parsed.needs_review,
            )
        )


def parse_and_persist_document(session: Session, filing_document: FilingDocument) -> ParseResult:
    filing_document.parser_started_at = datetime.now(timezone.utc)
    filing_document.parser_status = ParserStatus.PENDING
    session.flush()
    result = parse_filing_document(filing_document)
    persist_parse_result(session, filing_document, result)
    session.commit()
    return result


def parse_document_by_id(document_id: int) -> ParseResult:
    with SessionLocal() as session:
        filing_document = session.get(FilingDocument, document_id)
        if filing_document is None:
            raise ValueError(f"FilingDocument not found: {document_id}")
        return parse_and_persist_document(session, filing_document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse a stored filing document by database id.")
    parser.add_argument("document_id", type=int)
    args = parser.parse_args(argv)
    result = parse_document_by_id(args.document_id)
    print(result)
    return 1 if result.status in {ParserStatus.FAILED, ParserStatus.NEEDS_REVIEW} else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
