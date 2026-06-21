from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.enrichment.base import EnrichmentCandidate, EnrichmentSource

SEC_SOURCE = "sec_edgar"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_TIMEOUT = 30.0


class SecEdgarError(RuntimeError):
    """Raised when SEC EDGAR is unavailable or returns an unexpected response."""

    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def normalize_ticker(ticker: str | None) -> str | None:
    if not ticker:
        return None
    return ticker.strip().upper().replace(".", "-") or None


def cik_to_padded(cik: int | str) -> str:
    return str(cik).strip().zfill(10)


def cik_to_archive_path(cik: int | str) -> str:
    return str(int(str(cik).strip()))


def parse_sec_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_sec_datetime(value: Any) -> datetime | None:
    parsed = parse_sec_date(value)
    if parsed is None:
        return None
    return datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc)


def accession_without_dashes(accession_number: str) -> str:
    return accession_number.replace("-", "")


def filing_url(
    *,
    cik: str,
    accession_number: str,
    primary_document: str | None = None,
    archives_base_url: str = SEC_ARCHIVES_BASE_URL,
) -> str:
    base = (
        f"{archives_base_url.rstrip('/')}/"
        f"{cik_to_archive_path(cik)}/"
        f"{accession_without_dashes(accession_number)}"
    )
    if primary_document:
        return f"{base}/{primary_document}"
    return f"{base}/{accession_number}-index.html"


def _require_json_object(response: httpx.Response, reason_prefix: str) -> dict[str, Any]:
    if response.status_code in {403, 429, 500, 502, 503, 504}:
        raise SecEdgarError(f"{reason_prefix}_unavailable", status_code=response.status_code)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise SecEdgarError(f"{reason_prefix}_non_json_response", status_code=response.status_code) from exc
    if not isinstance(payload, dict):
        raise SecEdgarError(f"{reason_prefix}_unexpected_json_shape", status_code=response.status_code)
    return payload


class SecEdgarSource(EnrichmentSource):
    source = SEC_SOURCE

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        user_agent: str | None = None,
        company_tickers_url: str = SEC_COMPANY_TICKERS_URL,
        submissions_base_url: str = SEC_SUBMISSIONS_BASE_URL,
        archives_base_url: str = SEC_ARCHIVES_BASE_URL,
    ) -> None:
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        self.user_agent = user_agent or settings.sec_user_agent
        self.company_tickers_url = company_tickers_url
        self.submissions_base_url = submissions_base_url.rstrip("/")
        self.archives_base_url = archives_base_url.rstrip("/")
        self._ticker_map: dict[str, dict[str, Any]] | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }

    def fetch_company_tickers(self, *, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        if self._ticker_map is not None and not force_refresh:
            return self._ticker_map

        response = self.client.get(self.company_tickers_url, headers=self.headers)
        payload = _require_json_object(response, "sec_company_tickers")
        ticker_map: dict[str, dict[str, Any]] = {}
        for company in payload.values():
            if not isinstance(company, dict):
                continue
            ticker = normalize_ticker(company.get("ticker"))
            cik = company.get("cik_str")
            if not ticker or cik is None:
                continue
            ticker_map[ticker] = {
                "cik": cik_to_padded(cik),
                "ticker": ticker,
                "title": company.get("title"),
            }
        self._ticker_map = ticker_map
        return ticker_map

    def ticker_to_cik(self, ticker: str) -> str | None:
        normalized = normalize_ticker(ticker)
        if not normalized:
            return None
        company = self.fetch_company_tickers().get(normalized)
        if not company:
            return None
        return company["cik"]

    def submissions_url(self, cik: str) -> str:
        return f"{self.submissions_base_url}/CIK{cik_to_padded(cik)}.json"

    def fetch_submissions(self, cik: str) -> dict[str, Any]:
        response = self.client.get(self.submissions_url(cik), headers=self.headers)
        return _require_json_object(response, "sec_submissions")

    def fetch_for_ticker(self, ticker: str, *, forms: list[str] | None = None) -> list[EnrichmentCandidate]:
        normalized_ticker = normalize_ticker(ticker)
        if not normalized_ticker:
            return []
        cik = self.ticker_to_cik(normalized_ticker)
        if cik is None:
            return []
        submissions = self.fetch_submissions(cik)
        return self.parse_recent_filings(
            ticker=normalized_ticker,
            cik=cik,
            submissions=submissions,
            forms=forms or settings.sec_recent_forms,
        )

    def parse_recent_filings(
        self,
        *,
        ticker: str,
        cik: str,
        submissions: dict[str, Any],
        forms: list[str],
    ) -> list[EnrichmentCandidate]:
        recent = submissions.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            raise SecEdgarError("sec_submissions_missing_recent_filings")

        allowed_forms = {form.upper() for form in forms}
        accession_numbers = list(recent.get("accessionNumber") or [])
        candidates: list[EnrichmentCandidate] = []
        for idx, accession_number in enumerate(accession_numbers):
            form_type = self._recent_value(recent, "form", idx)
            if allowed_forms and str(form_type or "").upper() not in allowed_forms:
                continue
            filed_at = parse_sec_datetime(self._recent_value(recent, "filingDate", idx))
            event_date = parse_sec_date(self._recent_value(recent, "reportDate", idx)) or (
                filed_at.date() if filed_at else None
            )
            primary_document = self._recent_value(recent, "primaryDocument", idx)
            title = self._title_for_form(form_type, ticker)
            candidates.append(
                EnrichmentCandidate(
                    source=SEC_SOURCE,
                    external_id=str(accession_number),
                    ticker=ticker,
                    cik=cik,
                    event_type="sec_filing",
                    form_type=str(form_type) if form_type else None,
                    title=title,
                    summary=self._summary_for_form(form_type, ticker, filed_at),
                    filed_at=filed_at,
                    event_date=event_date,
                    url=filing_url(
                        cik=cik,
                        accession_number=str(accession_number),
                        primary_document=str(primary_document) if primary_document else None,
                        archives_base_url=self.archives_base_url,
                    ),
                    raw_payload={
                        "company_name": submissions.get("name"),
                        "ticker": ticker,
                        "cik": cik,
                        "recent_index": idx,
                        "filing": {
                            key: self._recent_value(recent, key, idx)
                            for key in recent.keys()
                            if isinstance(recent.get(key), list)
                        },
                    },
                    confidence=0.95,
                )
            )
        return candidates

    @staticmethod
    def _recent_value(recent: dict[str, Any], key: str, idx: int) -> Any | None:
        values = recent.get(key)
        if not isinstance(values, list) or idx >= len(values):
            return None
        return values[idx]

    @staticmethod
    def _title_for_form(form_type: Any, ticker: str) -> str:
        form = str(form_type or "SEC filing")
        return f"{ticker} {form} filed with SEC"

    @staticmethod
    def _summary_for_form(form_type: Any, ticker: str, filed_at: datetime | None) -> str:
        form = str(form_type or "filing")
        date_part = filed_at.date().isoformat() if filed_at else "recently"
        return f"{ticker} filed SEC form {form} on {date_part}."

