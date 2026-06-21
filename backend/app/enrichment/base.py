from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class EnrichmentCandidate:
    """Normalized item returned by an external enrichment source."""

    source: str
    external_id: str
    ticker: str | None
    cik: str | None
    event_type: str
    form_type: str | None = None
    title: str | None = None
    summary: str | None = None
    filed_at: datetime | None = None
    event_date: date | None = None
    url: str | None = None
    raw_payload: dict[str, Any] | None = None
    confidence: float | None = None


class EnrichmentSource(ABC):
    """Interface for external context sources such as SEC EDGAR or news APIs."""

    source: str

    @abstractmethod
    def fetch_for_ticker(self, ticker: str, *, forms: list[str] | None = None) -> list[EnrichmentCandidate]:
        """Fetch recent enrichment candidates for a ticker."""

