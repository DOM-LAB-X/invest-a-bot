from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.models import Alert, AlertEnrichment, AlertStatus, EnrichmentItem
from app.enrichment.base import EnrichmentCandidate, EnrichmentSource
from app.enrichment.sec_edgar import SecEdgarError
from app.services.enrichment import (
    build_enrichment_item,
    enrich_alerts,
    link_alert_enrichment,
    relevant_candidates_for_alert,
    upsert_enrichment_item,
)
from app.services.enrichment_matching import relevance_for_alert


def alert(**overrides) -> Alert:
    values = {
        "id": 10,
        "profile_id": 1,
        "profile_rule_id": 2,
        "transaction_id": 3,
        "filing_document_id": 4,
        "status": AlertStatus.NEW,
        "score": 75,
        "matched_at": datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc),
        "ticker_snapshot": "AAPL",
        "asset_name_snapshot": "Apple Inc.",
    }
    values.update(overrides)
    return Alert(**values)


def candidate(**overrides) -> EnrichmentCandidate:
    values = {
        "source": "sec_edgar",
        "external_id": "0000320193-26-000100",
        "ticker": "AAPL",
        "cik": "0000320193",
        "event_type": "sec_filing",
        "form_type": "8-K",
        "title": "AAPL 8-K filed with SEC",
        "summary": "AAPL filed SEC form 8-K on 2026-06-20.",
        "filed_at": datetime(2026, 6, 20, tzinfo=timezone.utc),
        "event_date": date(2026, 6, 20),
        "url": "https://www.sec.gov/Archives/edgar/data/320193/...",
        "raw_payload": {"filing": {"form": "8-K"}},
        "confidence": 0.95,
    }
    values.update(overrides)
    return EnrichmentCandidate(**values)


def test_relevance_for_alert_scores_same_ticker_material_filings() -> None:
    relevance = relevance_for_alert(alert(), candidate())

    assert relevance is not None
    assert relevance.score == 95
    assert "same ticker AAPL" in relevance.reason
    assert "8-K" in relevance.reason
    assert "within 7 days" in relevance.reason


def test_relevance_for_alert_rejects_ticker_mismatch() -> None:
    assert relevance_for_alert(alert(ticker_snapshot="AMZN"), candidate(ticker="AAPL")) is None


def test_relevant_candidates_filters_below_threshold() -> None:
    old_8k = candidate(
        external_id="old-8k",
        form_type="8-K",
        filed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        event_date=date(2025, 1, 1),
    )

    scored, below_threshold, capped = relevant_candidates_for_alert(
        alert(),
        [old_8k],
        min_relevance_score=60.0,
        max_items=10,
    )

    assert scored == []
    assert below_threshold == 1
    assert capped == 0


def test_relevant_candidates_caps_to_highest_score_then_recency() -> None:
    recent_8k = candidate(
        external_id="recent-8k",
        form_type="8-K",
        filed_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        event_date=date(2026, 6, 20),
    )
    recent_10q = candidate(
        external_id="recent-10q",
        form_type="10-Q",
        filed_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        event_date=date(2026, 6, 19),
    )
    old_8k = candidate(
        external_id="old-8k",
        form_type="8-K",
        filed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        event_date=date(2026, 1, 1),
    )

    scored, below_threshold, capped = relevant_candidates_for_alert(
        alert(),
        [old_8k, recent_10q, recent_8k],
        min_relevance_score=0,
        max_items=2,
    )

    assert [item.candidate.external_id for item in scored] == ["recent-8k", "recent-10q"]
    assert below_threshold == 0
    assert capped == 1


def test_build_enrichment_item_maps_candidate_fields() -> None:
    item = build_enrichment_item(candidate())

    assert item.source == "sec_edgar"
    assert item.external_id == "0000320193-26-000100"
    assert item.ticker == "AAPL"
    assert item.raw_payload == {"filing": {"form": "8-K"}}


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.added = []
        self.flushed = False
        self.committed = False
        self.next_id = 100

    def execute(self, _statement):
        if not self.responses:
            raise AssertionError("No fake session response queued")
        return _ScalarResult(self.responses.pop(0))

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushed = True
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = self.next_id
                self.next_id += 1

    def commit(self):
        self.committed = True


def test_upsert_enrichment_item_returns_existing_without_add() -> None:
    existing = EnrichmentItem(id=1, source="sec_edgar", external_id="existing", event_type="sec_filing")
    session = _FakeSession([existing])

    item, created = upsert_enrichment_item(session, candidate(external_id="existing"))

    assert item is existing
    assert created is False
    assert session.added == []


def test_upsert_enrichment_item_adds_new_item() -> None:
    session = _FakeSession([None])

    item, created = upsert_enrichment_item(session, candidate())

    assert created is True
    assert item.id == 100
    assert item in session.added
    assert session.flushed is True


def test_link_alert_enrichment_is_idempotent() -> None:
    existing = AlertEnrichment(id=1, alert_id=10, enrichment_item_id=20, relevance_score=80)
    session = _FakeSession([existing])

    link, created = link_alert_enrichment(
        session,
        alert=alert(),
        item=EnrichmentItem(id=20, source="sec_edgar", external_id="x", event_type="sec_filing"),
        relevance_score=80,
        reason="existing",
    )

    assert link is existing
    assert created is False
    assert session.added == []


class _StaticSource(EnrichmentSource):
    source = "sec_edgar"

    def __init__(self, items):
        self.items = items
        self.requested_tickers = []

    def fetch_for_ticker(self, ticker: str, *, forms: list[str] | None = None):
        self.requested_tickers.append((ticker, forms))
        return self.items


def test_enrich_alerts_persists_items_and_links_idempotently() -> None:
    session = _FakeSession(
        [
            [alert()],  # alerts_for_enrichment
            None,  # upsert item
            None,  # link alert enrichment
        ]
    )
    source = _StaticSource([candidate()])

    result = enrich_alerts(session, source=source, forms=["8-K"], force=True)

    assert source.requested_tickers == [("AAPL", ["8-K"])]
    assert result.alerts_considered == 1
    assert result.source_items_seen == 1
    assert result.items_created == 1
    assert result.links_created == 1
    assert result.errors == []
    assert isinstance(session.added[0], EnrichmentItem)
    assert isinstance(session.added[1], AlertEnrichment)
    assert session.committed is True


def test_enrich_alerts_does_not_persist_candidates_below_threshold() -> None:
    weak_candidate = candidate(
        external_id="old-8k",
        form_type="8-K",
        filed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        event_date=date(2025, 1, 1),
    )
    session = _FakeSession([[alert()]])
    source = _StaticSource([weak_candidate])

    result = enrich_alerts(session, source=source, min_relevance_score=60.0, force=True)

    assert result.source_items_seen == 1
    assert result.below_relevance_threshold == 1
    assert result.items_created == 0
    assert result.links_created == 0
    assert session.added == []
    assert session.committed is True


def test_enrich_alerts_caps_persisted_candidates_per_alert() -> None:
    candidates = [
        candidate(
            external_id="recent-8k",
            form_type="8-K",
            filed_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
            event_date=date(2026, 6, 20),
        ),
        candidate(
            external_id="recent-10q",
            form_type="10-Q",
            filed_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
            event_date=date(2026, 6, 19),
        ),
        candidate(
            external_id="old-8k",
            form_type="8-K",
            filed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            event_date=date(2026, 1, 1),
        ),
    ]
    session = _FakeSession(
        [
            [alert()],  # alerts_for_enrichment
            None,  # upsert recent-8k
            None,  # link recent-8k
            None,  # upsert recent-10q
            None,  # link recent-10q
        ]
    )
    source = _StaticSource(candidates)

    result = enrich_alerts(session, source=source, min_relevance_score=0, max_items_per_alert=2, force=True)

    persisted_items = [item for item in session.added if isinstance(item, EnrichmentItem)]
    assert [item.external_id for item in persisted_items] == ["recent-8k", "recent-10q"]
    assert result.source_items_seen == 3
    assert result.capped_items == 1
    assert result.items_created == 2
    assert result.links_created == 2


def test_enrich_alerts_records_source_errors_and_continues() -> None:
    class _UnavailableSource(EnrichmentSource):
        source = "sec_edgar"

        def fetch_for_ticker(self, ticker: str, *, forms: list[str] | None = None):
            raise SecEdgarError("sec_submissions_unavailable", status_code=503)

    session = _FakeSession([[alert()]])

    result = enrich_alerts(session, source=_UnavailableSource(), force=True)

    assert result.alerts_considered == 1
    assert result.errors == ["AAPL: sec_submissions_unavailable:503"]
    assert session.added == []
    assert session.committed is True
