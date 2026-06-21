from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FilingDocument, ParserStatus
from app.db.session import SessionLocal
from app.ingestion.base import DisclosureIngestionAdapter, IngestionResult, SourceDocument, StoredDocument
from app.ingestion.house_clerk import sha256_bytes

SENATE_SOURCE = "senate_efd"
SENATE_CHAMBER = "senate"
SENATE_HOME_URL = "https://efdsearch.senate.gov/search/home/"
SENATE_SEARCH_URL = "https://efdsearch.senate.gov/search/"
SENATE_DATA_URL = "https://efdsearch.senate.gov/search/report/data/"
PERIODIC_TRANSACTION_REPORT_TYPE = 11
DEFAULT_TIMEOUT = 30.0

CSRF_RE = re.compile(r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)["\']', re.IGNORECASE)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
HTML_RE = re.compile(r"<html|<!doctype html|<title", re.IGNORECASE)


class SourceUnavailableError(RuntimeError):
    """Raised when Senate eFD's data backend is unavailable or returns non-JSON maintenance HTML."""

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def normalize_whitespace(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def parse_senate_date(value: Any) -> date | None:
    value = normalize_whitespace(value)
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_senate_datetime(value: Any) -> datetime | None:
    parsed = parse_senate_date(value)
    if parsed is None:
        return None
    return datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc)


def format_senate_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%m/%d/%Y")
    return value


def extract_csrf_token(html: str) -> str:
    match = CSRF_RE.search(html)
    if not match:
        raise ValueError("Could not find csrfmiddlewaretoken in Senate eFD HTML")
    return match.group(1)


def absolute_senate_url(value: str) -> str:
    return urljoin(SENATE_SEARCH_URL, value)


def is_source_unavailable_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    text = response.text[:2000]
    return (
        response.status_code == 503
        or ("text/html" in content_type and HTML_RE.search(text) is not None)
        or "Site Under Maintenance" in text
    )


def response_json_or_raise(response: httpx.Response) -> dict[str, Any]:
    if is_source_unavailable_response(response):
        raise SourceUnavailableError(
            "senate_efd_source_unavailable",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceUnavailableError(
            "senate_efd_non_json_response",
            status_code=response.status_code,
        ) from exc
    if not isinstance(data, dict):
        raise SourceUnavailableError("senate_efd_unexpected_json_shape", status_code=response.status_code)
    return data


def row_payload(row: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    return {f"col_{idx}": value for idx, value in enumerate(row)}


def text_from_html(value: Any) -> str | None:
    value = normalize_whitespace(value)
    if not value:
        return None
    value = re.sub(r"<[^>]+>", " ", value)
    return normalize_whitespace(value)


def links_from_value(value: Any) -> list[str]:
    value = str(value or "")
    links = HREF_RE.findall(value)
    if value.startswith(("http://", "https://", "/")):
        links.append(value)
    return [absolute_senate_url(link) for link in links]


def links_from_row(row: dict[str, Any] | list[Any]) -> list[str]:
    payload = row_payload(row)
    links: list[str] = []
    for value in payload.values():
        links.extend(links_from_value(value))
    seen: set[str] = set()
    deduped: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
    return deduped


def first_payload_value(payload: dict[str, Any], *keys: str) -> Any | None:
    lower_payload = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        if key.lower() in lower_payload:
            return lower_payload[key.lower()]
    return None


def stable_source_document_id(row: dict[str, Any] | list[Any], source_url: str | None = None) -> str:
    payload = row_payload(row)
    explicit = first_payload_value(
        payload,
        "report_id",
        "reportId",
        "document_id",
        "documentId",
        "filing_id",
        "id",
    )
    if explicit:
        return str(explicit)
    for link in links_from_row(row):
        match = re.search(r"/(?:view|report|ptr|download)/([^/?#]+)", link)
        if match:
            return match.group(1)
    basis = source_url or repr(sorted(payload.items()))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def inferred_filer_name(row: dict[str, Any] | list[Any]) -> str:
    payload = row_payload(row)
    explicit = first_payload_value(
        payload,
        "senator_full_name",
        "filer_name",
        "name",
        "full_name",
        "candidate_name",
    )
    if explicit:
        return text_from_html(explicit) or "Unknown Filer"

    # Inferred until the live DataTables endpoint returns real row data again.
    for value in payload.values():
        text = text_from_html(value)
        if text and not parse_senate_date(text) and "report" not in text.casefold():
            return text
    return "Unknown Filer"


def inferred_filing_date(row: dict[str, Any] | list[Any]) -> date | None:
    payload = row_payload(row)
    explicit = first_payload_value(
        payload,
        "submitted_date",
        "date_received",
        "filing_date",
        "report_date",
        "date",
    )
    parsed = parse_senate_date(explicit)
    if parsed:
        return parsed
    for value in payload.values():
        parsed = parse_senate_date(value)
        if parsed:
            return parsed
    return None


def existing_source_document_ids(session: Session, documents: list[SourceDocument]) -> set[str]:
    source_document_ids = {document.source_document_id for document in documents}
    if not source_document_ids:
        return set()

    rows = session.execute(
        select(FilingDocument.source_document_id).where(
            FilingDocument.source == SENATE_SOURCE,
            FilingDocument.source_document_id.in_(source_document_ids),
        )
    )
    return set(rows.scalars().all())


class SenateEfdAdapter(DisclosureIngestionAdapter):
    source = SENATE_SOURCE

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        storage_dir: str | Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        self.storage_dir = Path(storage_dir or settings.pdf_storage_dir)
        self.enabled = settings.senate_ingestion_enabled if enabled is None else enabled
        self.session_established = False

    def fetch_home_csrf(self) -> str:
        response = self.client.get(SENATE_HOME_URL)
        response.raise_for_status()
        return extract_csrf_token(response.text)

    def accept_prohibition_agreement(self) -> None:
        token = self.fetch_home_csrf()
        response = self.client.post(
            SENATE_HOME_URL,
            data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
            headers={"Referer": SENATE_HOME_URL},
        )
        response.raise_for_status()

    def current_csrf_token(self) -> str:
        token = self.client.cookies.get("csrftoken")
        if not token:
            raise ValueError("Senate eFD csrftoken cookie is missing")
        return token

    def establish_session(self) -> None:
        self.accept_prohibition_agreement()
        response = self.client.get(SENATE_SEARCH_URL)
        response.raise_for_status()
        self.session_established = True

    def build_search_payload(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
        draw: int = 1,
        start: int = 0,
        length: int = 100,
        report_types: list[int] | None = None,
    ) -> dict[str, str]:
        report_types = report_types or [PERIODIC_TRANSACTION_REPORT_TYPE]
        return {
            "report_types": json.dumps(report_types),
            "filer_types": "[]",
            "submitted_start_date": format_senate_date(start_date),
            "submitted_end_date": format_senate_date(end_date),
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
            "draw": str(draw),
            "start": str(start),
            "length": str(length),
        }

    def search_reports_page(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
        draw: int,
        start: int,
        length: int,
        report_types: list[int] | None = None,
    ) -> dict[str, Any]:
        if not self.session_established:
            self.establish_session()

        response = self.client.post(
            SENATE_DATA_URL,
            data=self.build_search_payload(
                start_date=start_date,
                end_date=end_date,
                draw=draw,
                start=start,
                length=length,
                report_types=report_types,
            ),
            headers={
                "X-CSRFToken": self.current_csrf_token(),
                "Referer": SENATE_SEARCH_URL,
            },
        )
        return response_json_or_raise(response)

    def search_reports(
        self,
        *,
        start_date: date | str,
        end_date: date | str,
        page_size: int = 100,
        report_types: list[int] | None = None,
    ) -> list[dict[str, Any] | list[Any]]:
        rows: list[dict[str, Any] | list[Any]] = []
        start = 0
        draw = 1
        while True:
            page = self.search_reports_page(
                start_date=start_date,
                end_date=end_date,
                draw=draw,
                start=start,
                length=page_size,
                report_types=report_types,
            )
            data = page.get("data") or []
            if not isinstance(data, list):
                raise SourceUnavailableError("senate_efd_unexpected_data_shape")
            rows.extend(data)
            records_filtered = page.get("recordsFiltered")
            if isinstance(records_filtered, int):
                if start + len(data) >= records_filtered:
                    break
            elif len(data) < page_size:
                break
            if not data:
                break
            start += page_size
            draw += 1
        return rows

    def resolve_document_url(self, row: dict[str, Any] | list[Any]) -> str | None:
        links = links_from_row(row)
        for link in links:
            if ".pdf" in link.casefold():
                return link
        if not links:
            return None

        detail_url = links[0]
        response = self.client.get(detail_url, headers={"Referer": SENATE_SEARCH_URL})
        response.raise_for_status()
        detail_links = links_from_value(response.text)
        for link in detail_links:
            if ".pdf" in link.casefold():
                return link
        return detail_url

    def parse_report_row(self, row: dict[str, Any] | list[Any]) -> SourceDocument:
        source_url = self.resolve_document_url(row)
        payload = row_payload(row)
        filing_date = inferred_filing_date(row)
        source_document_id = stable_source_document_id(row, source_url)
        return SourceDocument(
            source=SENATE_SOURCE,
            source_document_id=source_document_id,
            source_url=source_url or SENATE_DATA_URL,
            index_url=SENATE_DATA_URL,
            filing_type="periodic_transaction_report",
            filing_year=filing_date.year if filing_date else None,
            filer_name=inferred_filer_name(row),
            chamber=SENATE_CHAMBER,
            filed_at=parse_senate_datetime(filing_date),
            document_date=filing_date,
            raw_index_payload=payload,
        )

    def parse_report_rows(self, rows: list[dict[str, Any] | list[Any]]) -> list[SourceDocument]:
        return [self.parse_report_row(row) for row in rows]

    def fetch_index(self, year: int) -> list[SourceDocument]:
        return self.search_periodic_transaction_reports(date(year, 1, 1), date(year, 12, 31))

    def search_periodic_transaction_reports(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        page_size: int = 100,
    ) -> list[SourceDocument]:
        rows = self.search_reports(
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            report_types=[PERIODIC_TRANSACTION_REPORT_TYPE],
        )
        return self.parse_report_rows(rows)

    def download_pdf(self, document: SourceDocument) -> bytes:
        response = self.client.get(document.source_url, headers={"Referer": SENATE_SEARCH_URL})
        response.raise_for_status()
        return response.content

    def storage_path_for(self, document: SourceDocument) -> Path:
        year = document.filing_year or "unknown-year"
        return self.storage_dir / SENATE_SOURCE / str(year) / f"{document.source_document_id}.pdf"

    def store_pdf(self, document: SourceDocument, content: bytes) -> StoredDocument:
        storage_path = self.storage_path_for(document)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_bytes(content)
        existed = storage_path.exists()
        changed = not existed or sha256_bytes(storage_path.read_bytes()) != digest
        if changed:
            storage_path.write_bytes(content)
        return StoredDocument(
            metadata=document,
            storage_path=storage_path,
            sha256=digest,
            size_bytes=len(content),
            created=not existed,
            changed=changed,
        )

    def persist_document(self, session: Session, stored: StoredDocument) -> FilingDocument:
        document = stored.metadata
        existing = session.execute(
            select(FilingDocument).where(
                FilingDocument.source == document.source,
                FilingDocument.source_document_id == document.source_document_id,
            )
        ).scalar_one_or_none()

        if existing is None:
            existing = FilingDocument(
                source=document.source,
                source_document_id=document.source_document_id,
                source_url=document.source_url,
                filer_name=document.filer_name,
                chamber=document.chamber,
            )
            session.add(existing)

        changed_hash = existing.pdf_sha256 != stored.sha256
        for key, value in asdict(document).items():
            if hasattr(existing, key):
                setattr(existing, key, value)

        existing.pdf_sha256 = stored.sha256
        existing.pdf_size_bytes = stored.size_bytes
        existing.storage_path = str(stored.storage_path)
        if changed_hash:
            existing.parser_status = ParserStatus.PENDING
            existing.parser_version = None
            existing.parser_started_at = None
            existing.parser_finished_at = None
            existing.parser_error = None
            existing.parser_confidence = None
            existing.transaction_count = 0

        session.flush()
        stored.filing_document_id = existing.id
        return existing

    def ingest_range(
        self,
        session: Session,
        *,
        start_date: date | str,
        end_date: date | str,
        page_size: int = 100,
        allow_disabled: bool = False,
    ) -> IngestionResult:
        result = IngestionResult()
        if not self.enabled and not allow_disabled:
            result.errors.append("senate_ingestion_disabled")
            return result

        try:
            documents = self.search_periodic_transaction_reports(start_date, end_date, page_size=page_size)
        except SourceUnavailableError as exc:
            result.errors.append(f"{exc.reason}:{exc.status_code}")
            return result

        result.discovered = len(documents)
        existing_doc_ids = existing_source_document_ids(session, documents)
        for document in documents:
            if document.source_document_id in existing_doc_ids:
                result.skipped += 1
                continue
            try:
                content = self.download_pdf(document)
                stored = self.store_pdf(document, content)
                self.persist_document(session, stored)
                result.stored += 1
                if stored.created:
                    result.created += 1
                elif stored.changed:
                    result.changed += 1
                else:
                    result.unchanged += 1
            except Exception as exc:  # pragma: no cover - defensive summary for manual runs
                result.errors.append(f"{document.source_document_id}: {exc}")

        session.commit()
        return result

    def ingest_year(self, session: Session, year: int) -> IngestionResult:
        return self.ingest_range(session, start_date=date(year, 1, 1), end_date=date(year, 12, 31))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest Senate eFD periodic transaction reports for a date range.")
    parser.add_argument("start_date", help="Start date as MM/DD/YYYY or YYYY-MM-DD")
    parser.add_argument("end_date", help="End date as MM/DD/YYYY or YYYY-MM-DD")
    parser.add_argument("--allow-disabled", action="store_true", help="Run even when senate_ingestion_enabled=False")
    args = parser.parse_args(argv)
    adapter = SenateEfdAdapter()
    with SessionLocal() as db:
        result = adapter.ingest_range(
            db,
            start_date=args.start_date,
            end_date=args.end_date,
            allow_disabled=args.allow_disabled,
        )
    print(result)
    return 1 if result.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
