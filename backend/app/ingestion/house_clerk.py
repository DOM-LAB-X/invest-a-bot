from __future__ import annotations

import argparse
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FilingDocument, ParserStatus
from app.db.session import SessionLocal
from app.ingestion.base import DisclosureIngestionAdapter, IngestionResult, SourceDocument, StoredDocument

HOUSE_SOURCE = "house_clerk"
HOUSE_CHAMBER = "house"
DEFAULT_TIMEOUT = 30.0


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def parse_house_date(value: str | None) -> date | None:
    value = normalize_whitespace(value)
    if not value:
        return None

    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_house_datetime(value: str | None) -> datetime | None:
    parsed_date = parse_house_date(value)
    if parsed_date is None:
        return None
    return datetime.combine(parsed_date, datetime.min.time(), tzinfo=timezone.utc)


def split_state_district(value: str | None) -> tuple[str | None, str | None]:
    value = normalize_whitespace(value)
    if not value:
        return None, None
    match = re.match(r"^([A-Z]{2})(.*)$", value.upper())
    if not match:
        return None, value
    state, district = match.groups()
    return state, district or None


def normalize_filing_type(value: str | None) -> str | None:
    value = normalize_whitespace(value)
    if value is None:
        return None
    mapping = {
        "P": "periodic_transaction_report",
        "PTR": "periodic_transaction_report",
        "A": "annual",
        "FD": "annual",
        "E": "extension",
        "T": "termination",
    }
    return mapping.get(value.upper(), value)


def _child_text(element: ET.Element, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for child in list(element):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in wanted:
            return normalize_whitespace(child.text)
    return None


def _element_payload(element: ET.Element) -> dict[str, str | None]:
    payload: dict[str, str | None] = {}
    for child in list(element):
        tag = child.tag.rsplit("}", 1)[-1]
        payload[tag] = normalize_whitespace(child.text)
    return payload


def _filer_name(element: ET.Element, payload: dict[str, str | None]) -> str:
    full_name = (
        payload.get("Name")
        or payload.get("MemberName")
        or payload.get("FilerName")
        or payload.get("Representative")
    )
    if full_name:
        return full_name

    parts = [
        _child_text(element, "Prefix"),
        _child_text(element, "First"),
        _child_text(element, "Middle"),
        _child_text(element, "Last"),
        _child_text(element, "Suffix"),
    ]
    return normalize_whitespace(" ".join(part for part in parts if part)) or "Unknown Filer"


def house_index_url(year: int, base_url: str | None = None) -> str:
    base = (base_url or settings.house_clerk_base_url).rstrip("/")
    return f"{base}/{year}FD.xml"


def house_pdf_url(doc_id: str, year: int, filing_type: str | None = None) -> str:
    path = "ptr-pdfs" if filing_type == "periodic_transaction_report" else "financial-pdfs"
    return f"https://disclosures-clerk.house.gov/public_disc/{path}/{year}/{doc_id}.pdf"


def parse_house_index_xml(xml_content: str | bytes, *, year: int, index_url: str) -> list[SourceDocument]:
    root = ET.fromstring(xml_content)
    records: list[SourceDocument] = []

    for element in root.iter():
        payload = _element_payload(element)
        doc_id = (
            payload.get("DocID")
            or payload.get("DocumentID")
            or payload.get("DocumentId")
            or payload.get("DocId")
        )
        if not doc_id:
            continue

        raw_filing_type = payload.get("FilingType") or payload.get("ReportType") or payload.get("Type")
        filing_type = normalize_filing_type(raw_filing_type)
        state, district = split_state_district(payload.get("StateDst") or payload.get("StateDistrict"))
        filing_date = (
            payload.get("FilingDate")
            or payload.get("DateReceived")
            or payload.get("ReceivedDate")
            or payload.get("ReportDate")
        )

        records.append(
            SourceDocument(
                source=HOUSE_SOURCE,
                source_document_id=doc_id,
                source_url=house_pdf_url(doc_id, year, filing_type),
                index_url=index_url,
                filing_type=filing_type,
                filing_year=year,
                filer_name=_filer_name(element, payload),
                filer_bioguide_id=payload.get("BioGuideID") or payload.get("BioguideID"),
                filer_state=state,
                filer_district=district,
                chamber=HOUSE_CHAMBER,
                filed_at=parse_house_datetime(filing_date),
                document_date=parse_house_date(filing_date),
                raw_index_payload=payload,
            )
        )

    return records


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def existing_source_document_ids(session: Session, documents: list[SourceDocument]) -> set[str]:
    source_document_ids = {document.source_document_id for document in documents}
    if not source_document_ids:
        return set()

    rows = session.execute(
        select(FilingDocument.source_document_id).where(
            FilingDocument.source == HOUSE_SOURCE,
            FilingDocument.source_document_id.in_(source_document_ids),
        )
    )
    return set(rows.scalars().all())


class HouseClerkAdapter(DisclosureIngestionAdapter):
    source = HOUSE_SOURCE

    def __init__(
        self,
        *,
        base_url: str | None = None,
        storage_dir: str | Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or settings.house_clerk_base_url).rstrip("/")
        self.storage_dir = Path(storage_dir or settings.pdf_storage_dir)
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    def index_url(self, year: int) -> str:
        return house_index_url(year, self.base_url)

    def fetch_index(self, year: int) -> list[SourceDocument]:
        url = self.index_url(year)
        response = self.client.get(url)
        response.raise_for_status()
        return parse_house_index_xml(response.content, year=year, index_url=url)

    def download_pdf(self, document: SourceDocument) -> bytes:
        response = self.client.get(document.source_url)
        response.raise_for_status()
        return response.content

    def storage_path_for(self, document: SourceDocument) -> Path:
        year = document.filing_year or "unknown-year"
        return self.storage_dir / HOUSE_SOURCE / str(year) / f"{document.source_document_id}.pdf"

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

    def ingest_year(self, session: Session, year: int) -> IngestionResult:
        result = IngestionResult()
        documents = self.fetch_index(year)
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
            except Exception as exc:  # pragma: no cover - defensive summary for CLI/manual runs
                result.errors.append(f"{document.source_document_id}: {exc}")

        session.commit()
        return result


def ingest_house_year(year: int, *, session: Session | None = None) -> IngestionResult:
    adapter = HouseClerkAdapter()
    if session is not None:
        return adapter.ingest_year(session, year)

    with SessionLocal() as db:
        return adapter.ingest_year(db, year)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest House Clerk disclosure PDFs for a year.")
    parser.add_argument("year", type=int)
    args = parser.parse_args(argv)
    result = ingest_house_year(args.year)
    print(result)
    return 1 if result.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
