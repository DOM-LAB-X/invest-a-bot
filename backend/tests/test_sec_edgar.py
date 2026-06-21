from __future__ import annotations

from datetime import date

import httpx

from app.enrichment.sec_edgar import (
    SecEdgarError,
    SecEdgarSource,
    accession_without_dashes,
    cik_to_padded,
    filing_url,
)


def test_sec_edgar_fetches_mapping_submissions_and_builds_candidates() -> None:
    requested_user_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_user_agents.append(request.headers["user-agent"])
        if str(request.url) == "https://sec.test/files/company_tickers.json":
            return httpx.Response(
                200,
                json={
                    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                    "1": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon.com, Inc."},
                },
            )
        if str(request.url) == "https://data.test/submissions/CIK0000320193.json":
            return httpx.Response(
                200,
                json={
                    "cik": "0000320193",
                    "name": "Apple Inc.",
                    "filings": {
                        "recent": {
                            "accessionNumber": [
                                "0000320193-26-000100",
                                "0000320193-26-000090",
                                "0000320193-26-000080",
                            ],
                            "filingDate": ["2026-06-20", "2026-05-01", "2026-04-01"],
                            "reportDate": ["2026-06-18", "2026-03-31", "2026-03-15"],
                            "form": ["8-K", "10-Q", "S-8"],
                            "primaryDocument": ["aapl-20260620.htm", "aapl-10q.htm", "s8.htm"],
                        }
                    },
                },
            )
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = SecEdgarSource(
        client=client,
        user_agent="invest-a-bot test@example.com",
        company_tickers_url="https://sec.test/files/company_tickers.json",
        submissions_base_url="https://data.test/submissions",
        archives_base_url="https://archive.test/Archives/edgar/data",
    )

    candidates = source.fetch_for_ticker("aapl", forms=["8-K", "10-Q"])

    assert requested_user_agents == ["invest-a-bot test@example.com", "invest-a-bot test@example.com"]
    assert [candidate.external_id for candidate in candidates] == [
        "0000320193-26-000100",
        "0000320193-26-000090",
    ]
    assert candidates[0].ticker == "AAPL"
    assert candidates[0].cik == "0000320193"
    assert candidates[0].event_type == "sec_filing"
    assert candidates[0].form_type == "8-K"
    assert candidates[0].filed_at is not None
    assert candidates[0].event_date == date(2026, 6, 18)
    assert candidates[0].url == (
        "https://archive.test/Archives/edgar/data/320193/000032019326000100/aapl-20260620.htm"
    )
    assert candidates[0].raw_payload["company_name"] == "Apple Inc."


def test_sec_edgar_caches_ticker_mapping() -> None:
    mapping_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mapping_requests
        mapping_requests += 1
        return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = SecEdgarSource(
        client=client,
        user_agent="invest-a-bot test@example.com",
        company_tickers_url="https://sec.test/files/company_tickers.json",
    )

    assert source.ticker_to_cik("AAPL") == "0000320193"
    assert source.ticker_to_cik("aapl") == "0000320193"
    assert mapping_requests == 1


def test_sec_edgar_raises_source_error_for_sec_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = SecEdgarSource(client=client, company_tickers_url="https://sec.test/files/company_tickers.json")

    try:
        source.ticker_to_cik("AAPL")
    except SecEdgarError as exc:
        assert exc.reason == "sec_company_tickers_unavailable"
        assert exc.status_code == 503
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("Expected SecEdgarError")


def test_sec_helpers_normalize_cik_accession_and_filing_url() -> None:
    assert cik_to_padded(320193) == "0000320193"
    assert accession_without_dashes("0000320193-26-000100") == "000032019326000100"
    assert filing_url(cik="0000320193", accession_number="0000320193-26-000100") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/"
        "0000320193-26-000100-index.html"
    )

