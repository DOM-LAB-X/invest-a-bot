from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs

import httpx

from app.db.models import FilingDocument
from app.ingestion.base import SourceDocument, StoredDocument
from app.ingestion.senate_efd import (
    SENATE_HOME_URL,
    SENATE_SEARCH_URL,
    SourceUnavailableError,
    SenateEfdAdapter,
    parse_senate_date,
)


def _request_path(request: httpx.Request) -> str:
    return request.url.path


def _agreement_html(token: str = "home-token") -> str:
    return f"""
    <html>
      <form method="post">
        <input type="hidden" name="csrfmiddlewaretoken" value="{token}">
        <input type="checkbox" name="prohibition_agreement" value="1">
      </form>
    </html>
    """


def test_search_reports_uses_agreement_session_cookie_csrf_and_referer() -> None:
    data_post_payloads: list[dict[str, list[str]]] = []
    data_post_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and _request_path(request) == "/search/home/":
            return httpx.Response(200, text=_agreement_html())
        if request.method == "POST" and _request_path(request) == "/search/home/":
            body = parse_qs(request.content.decode())
            assert body["csrfmiddlewaretoken"] == ["home-token"]
            assert body["prohibition_agreement"] == ["1"]
            assert request.headers["referer"] == SENATE_HOME_URL
            return httpx.Response(200, text="accepted", headers={"set-cookie": "csrftoken=agreement-cookie; Path=/"})
        if request.method == "GET" and _request_path(request) == "/search/":
            return httpx.Response(200, text="search", headers={"set-cookie": "csrftoken=search-cookie; Path=/"})
        if request.method == "POST" and _request_path(request) == "/search/report/data/":
            data_post_headers.append(request.headers)
            data_post_payloads.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "draw": 1,
                    "recordsTotal": 1,
                    "recordsFiltered": 1,
                    "data": [
                        {
                            "report_id": "efd-123",
                            "senator_full_name": "Jane Senator",
                            "submitted_date": "06/20/2026",
                            "report_link": '<a href="/search/view/paper/efd-123.pdf">PDF</a>',
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://efdsearch.senate.gov")
    adapter = SenateEfdAdapter(client=client, enabled=True)

    rows = adapter.search_reports(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30), page_size=10)

    assert len(rows) == 1
    assert data_post_headers[0]["x-csrftoken"] == "search-cookie"
    assert data_post_headers[0]["referer"] == SENATE_SEARCH_URL
    payload = data_post_payloads[0]
    assert payload["report_types"] == ["[11]"]
    assert payload["submitted_start_date"] == ["06/01/2026"]
    assert payload["submitted_end_date"] == ["06/30/2026"]
    assert payload["start"] == ["0"]
    assert payload["length"] == ["10"]


def test_search_reports_detects_maintenance_html_as_source_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and _request_path(request) == "/search/home/":
            return httpx.Response(200, text=_agreement_html())
        if request.method == "POST" and _request_path(request) == "/search/home/":
            return httpx.Response(200, text="accepted", headers={"set-cookie": "csrftoken=agreement-cookie; Path=/"})
        if request.method == "GET" and _request_path(request) == "/search/":
            return httpx.Response(200, text="search")
        if request.method == "POST" and _request_path(request) == "/search/report/data/":
            return httpx.Response(
                503,
                text="<html><head><title>U.S. Senate: Site Under Maintenance</title></head></html>",
                headers={"content-type": "text/html"},
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = SenateEfdAdapter(client=client, enabled=True)

    try:
        adapter.search_reports(start_date="06/01/2026", end_date="06/30/2026")
    except SourceUnavailableError as exc:
        assert exc.reason == "senate_efd_source_unavailable"
        assert exc.status_code == 503
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("Expected SourceUnavailableError")


def test_search_reports_paginates_datatables_results() -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and _request_path(request) == "/search/home/":
            return httpx.Response(200, text=_agreement_html())
        if request.method == "POST" and _request_path(request) == "/search/home/":
            return httpx.Response(200, text="accepted", headers={"set-cookie": "csrftoken=token; Path=/"})
        if request.method == "GET" and _request_path(request) == "/search/":
            return httpx.Response(200, text="search")
        if request.method == "POST" and _request_path(request) == "/search/report/data/":
            payload = parse_qs(request.content.decode())
            starts.append(payload["start"][0])
            row_number = int(payload["start"][0])
            return httpx.Response(
                200,
                json={
                    "draw": row_number + 1,
                    "recordsTotal": 2,
                    "recordsFiltered": 2,
                    "data": [{"report_id": f"efd-{row_number}", "submitted_date": "06/20/2026"}],
                },
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = SenateEfdAdapter(client=client, enabled=True)

    rows = adapter.search_reports(start_date="06/01/2026", end_date="06/30/2026", page_size=1)

    assert [row["report_id"] for row in rows] == ["efd-0", "efd-1"]
    assert starts == ["0", "1"]


def test_parse_report_row_resolves_detail_page_pdf_and_infers_fields() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if _request_path(request) == "/search/view/ptr/efd-123/":
            return httpx.Response(200, text='<html><a href="/search/view/paper/efd-123.pdf">Download</a></html>')
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = SenateEfdAdapter(client=client, enabled=True)
    row = {
        # Inferred from public examples of Senate eFD DataTables responses until
        # the live endpoint returns row data again.
        "report_id": "efd-123",
        "senator_full_name": "Jane Senator",
        "report_type": "Periodic Transaction Report",
        "submitted_date": "06/20/2026",
        "report_link": '<a href="/search/view/ptr/efd-123/">View Report</a>',
    }

    document = adapter.parse_report_row(row)

    assert requested == ["https://efdsearch.senate.gov/search/view/ptr/efd-123/"]
    assert document.source == "senate_efd"
    assert document.source_document_id == "efd-123"
    assert document.source_url == "https://efdsearch.senate.gov/search/view/paper/efd-123.pdf"
    assert document.filer_name == "Jane Senator"
    assert document.chamber == "senate"
    assert document.filing_type == "periodic_transaction_report"
    assert document.document_date == date(2026, 6, 20)
    assert document.filing_year == 2026
    assert document.raw_index_payload == row


class _ExistingIdsResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _ExistingIdsSession:
    def __init__(self, existing_ids):
        self.existing_ids = existing_ids
        self.execute_calls = 0
        self.committed = False

    def execute(self, _statement):
        self.execute_calls += 1
        return _ExistingIdsResult(self.existing_ids)

    def commit(self):
        self.committed = True


class _RecordingSenateAdapter(SenateEfdAdapter):
    def __init__(self, documents: list[SourceDocument], *, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.documents = documents
        self.downloaded_doc_ids: list[str] = []
        self.persisted_doc_ids: list[str] = []

    def search_periodic_transaction_reports(self, start_date, end_date, *, page_size=100):
        return self.documents

    def download_pdf(self, document):
        self.downloaded_doc_ids.append(document.source_document_id)
        return f"pdf-{document.source_document_id}".encode()

    def store_pdf(self, document, content):
        return StoredDocument(
            metadata=document,
            storage_path=f"/tmp/{document.source_document_id}.pdf",
            sha256="hash",
            size_bytes=len(content),
            created=True,
            changed=True,
        )

    def persist_document(self, session, stored):
        self.persisted_doc_ids.append(stored.metadata.source_document_id)
        return FilingDocument(
            source=stored.metadata.source,
            source_document_id=stored.metadata.source_document_id,
            source_url=stored.metadata.source_url,
            filer_name=stored.metadata.filer_name,
            chamber=stored.metadata.chamber,
        )


def test_ingest_range_is_disabled_by_default_without_override() -> None:
    adapter = _RecordingSenateAdapter([], enabled=False)
    session = _ExistingIdsSession(existing_ids=[])

    result = adapter.ingest_range(session, start_date="06/01/2026", end_date="06/30/2026")

    assert result.errors == ["senate_ingestion_disabled"]
    assert session.execute_calls == 0
    assert session.committed is False


def test_ingest_range_skips_existing_documents_before_download() -> None:
    documents = [
        SourceDocument(
            source="senate_efd",
            source_document_id="existing-doc",
            source_url="https://efdsearch.senate.gov/search/view/paper/existing.pdf",
            filer_name="Existing Senator",
            chamber="senate",
        ),
        SourceDocument(
            source="senate_efd",
            source_document_id="new-doc",
            source_url="https://efdsearch.senate.gov/search/view/paper/new.pdf",
            filer_name="New Senator",
            chamber="senate",
        ),
    ]
    adapter = _RecordingSenateAdapter(documents, enabled=True)
    session = _ExistingIdsSession(existing_ids=["existing-doc"])

    result = adapter.ingest_range(session, start_date="06/01/2026", end_date="06/30/2026")

    assert session.execute_calls == 1
    assert adapter.downloaded_doc_ids == ["new-doc"]
    assert adapter.persisted_doc_ids == ["new-doc"]
    assert result.discovered == 2
    assert result.skipped == 1
    assert result.stored == 1
    assert result.created == 1
    assert session.committed is True


def test_ingest_range_reports_source_unavailable_without_marking_empty() -> None:
    class _UnavailableAdapter(SenateEfdAdapter):
        def search_periodic_transaction_reports(self, start_date, end_date, *, page_size=100):
            raise SourceUnavailableError("senate_efd_source_unavailable", status_code=503)

    adapter = _UnavailableAdapter(enabled=True)
    session = _ExistingIdsSession(existing_ids=[])

    result = adapter.ingest_range(session, start_date="06/01/2026", end_date="06/30/2026")

    assert result.discovered == 0
    assert result.errors == ["senate_efd_source_unavailable:503"]
    assert session.execute_calls == 0
    assert session.committed is False


def test_parse_senate_date_accepts_common_formats() -> None:
    assert parse_senate_date("06/20/2026") == date(2026, 6, 20)
    assert parse_senate_date("2026-06-20") == date(2026, 6, 20)
    assert parse_senate_date("06/20/26") == date(2026, 6, 20)
