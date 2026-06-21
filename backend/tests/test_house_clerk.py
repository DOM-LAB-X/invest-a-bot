from __future__ import annotations

import httpx

from app.db.models import FilingDocument, ParserStatus
from app.ingestion.house_clerk import (
    HouseClerkAdapter,
    house_pdf_url,
    parse_house_index_xml,
    sha256_bytes,
)


HOUSE_XML = """\
<FinancialDisclosure>
  <Member>
    <Prefix>Hon.</Prefix>
    <First>Jane</First>
    <Last>Doe</Last>
    <FilingType>P</FilingType>
    <StateDst>CA12</StateDst>
    <FilingDate>06/21/2026</FilingDate>
    <DocID>20024843</DocID>
  </Member>
  <Member>
    <Name>John Smith</Name>
    <FilingType>A</FilingType>
    <StateDst>NY03</StateDst>
    <FilingDate>2026-06-20</FilingDate>
    <DocID>10000001</DocID>
  </Member>
</FinancialDisclosure>
"""


def test_parse_house_index_xml_normalizes_records() -> None:
    records = parse_house_index_xml(HOUSE_XML, year=2026, index_url="https://example.test/2026FD.xml")

    assert len(records) == 2
    first = records[0]
    assert first.source == "house_clerk"
    assert first.source_document_id == "20024843"
    assert first.filer_name == "Hon. Jane Doe"
    assert first.filing_type == "periodic_transaction_report"
    assert first.filer_state == "CA"
    assert first.filer_district == "12"
    assert first.source_url == (
        "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024843.pdf"
    )
    assert first.document_date is not None

    second = records[1]
    assert second.filing_type == "annual"
    assert second.source_url == (
        "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026/10000001.pdf"
    )


def test_house_pdf_url_uses_ptr_directory_for_periodic_reports() -> None:
    assert house_pdf_url("123", 2026, "periodic_transaction_report").endswith("/ptr-pdfs/2026/123.pdf")
    assert house_pdf_url("123", 2026, "annual").endswith("/financial-pdfs/2026/123.pdf")


def test_fetch_index_uses_configured_xml_endpoint() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=HOUSE_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = HouseClerkAdapter(base_url="https://example.test/public_disc/financial-pdfs", client=client)

    records = adapter.fetch_index(2026)

    assert requested == ["https://example.test/public_disc/financial-pdfs/2026FD.xml"]
    assert [record.source_document_id for record in records] == ["20024843", "10000001"]


def test_store_pdf_writes_content_and_detects_unchanged(tmp_path) -> None:
    document = parse_house_index_xml(HOUSE_XML, year=2026, index_url="index")[0]
    adapter = HouseClerkAdapter(storage_dir=tmp_path)

    first = adapter.store_pdf(document, b"pdf-content")
    second = adapter.store_pdf(document, b"pdf-content")

    assert first.created is True
    assert first.changed is True
    assert second.created is False
    assert second.changed is False
    assert first.sha256 == sha256_bytes(b"pdf-content")
    assert first.storage_path.read_bytes() == b"pdf-content"


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


def test_persist_document_resets_parser_status_when_hash_changes(tmp_path) -> None:
    document = parse_house_index_xml(HOUSE_XML, year=2026, index_url="index")[0]
    adapter = HouseClerkAdapter(storage_dir=tmp_path)
    stored = adapter.store_pdf(document, b"new-pdf")
    existing = FilingDocument(
        source=document.source,
        source_document_id=document.source_document_id,
        source_url=document.source_url,
        filer_name=document.filer_name,
        chamber=document.chamber,
        pdf_sha256="old",
        parser_status=ParserStatus.PARSED,
        parser_version="old-parser",
        transaction_count=3,
    )
    session = _FakeSession(existing=existing)

    adapter.persist_document(session, stored)

    assert existing.pdf_sha256 == stored.sha256
    assert existing.parser_status == ParserStatus.PENDING
    assert existing.parser_version is None
    assert existing.transaction_count == 0
    assert session.flushed is True
