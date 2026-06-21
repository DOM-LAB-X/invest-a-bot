from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SourceDocument:
    """Normalized metadata for one filing discovered from a source index."""

    source: str
    source_document_id: str
    source_url: str
    filer_name: str
    chamber: str
    index_url: str | None = None
    filing_type: str | None = None
    filing_year: int | None = None
    filer_bioguide_id: str | None = None
    filer_state: str | None = None
    filer_district: str | None = None
    filed_at: Any | None = None
    reported_at: Any | None = None
    document_date: Any | None = None
    raw_index_payload: dict[str, Any] | None = None


@dataclass
class StoredDocument:
    """Result of downloading and persisting one raw filing document."""

    metadata: SourceDocument
    storage_path: Path
    sha256: str
    size_bytes: int
    created: bool
    changed: bool
    filing_document_id: int | None = None


@dataclass
class IngestionResult:
    """Summary from one source ingestion pass."""

    discovered: int = 0
    stored: int = 0
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class DisclosureIngestionAdapter(ABC):
    """Interface for source adapters that discover and store disclosure documents."""

    source: str

    @abstractmethod
    def fetch_index(self, year: int) -> list[SourceDocument]:
        """Fetch and normalize source index metadata for a year."""

    @abstractmethod
    def ingest_year(self, session: Session, year: int) -> IngestionResult:
        """Fetch source metadata, download new/changed documents, and persist records."""
