from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Alert, AlertEnrichment, AlertStatus, EnrichmentItem
from app.enrichment.base import EnrichmentCandidate, EnrichmentSource
from app.enrichment.sec_edgar import SecEdgarError, SecEdgarSource
from app.services.enrichment_matching import EnrichmentRelevance, relevance_for_alert


@dataclass
class EnrichmentRunResult:
    alerts_considered: int = 0
    alerts_without_ticker: int = 0
    source_items_seen: int = 0
    below_relevance_threshold: int = 0
    capped_items: int = 0
    items_created: int = 0
    links_created: int = 0
    links_existing: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoredEnrichmentCandidate:
    candidate: EnrichmentCandidate
    relevance: EnrichmentRelevance


def build_enrichment_item(candidate: EnrichmentCandidate) -> EnrichmentItem:
    return EnrichmentItem(
        source=candidate.source,
        external_id=candidate.external_id,
        ticker=candidate.ticker,
        cik=candidate.cik,
        event_type=candidate.event_type,
        form_type=candidate.form_type,
        title=candidate.title,
        summary=candidate.summary,
        filed_at=candidate.filed_at,
        event_date=candidate.event_date,
        url=candidate.url,
        raw_payload=candidate.raw_payload,
        confidence=candidate.confidence,
    )


def upsert_enrichment_item(session: Session, candidate: EnrichmentCandidate) -> tuple[EnrichmentItem, bool]:
    existing = session.execute(
        select(EnrichmentItem).where(
            EnrichmentItem.source == candidate.source,
            EnrichmentItem.external_id == candidate.external_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    item = build_enrichment_item(candidate)
    session.add(item)
    session.flush()
    return item, True


def link_alert_enrichment(
    session: Session,
    *,
    alert: Alert,
    item: EnrichmentItem,
    relevance_score: float,
    reason: str,
) -> tuple[AlertEnrichment, bool]:
    existing = session.execute(
        select(AlertEnrichment).where(
            AlertEnrichment.alert_id == alert.id,
            AlertEnrichment.enrichment_item_id == item.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    link = AlertEnrichment(
        alert_id=alert.id,
        enrichment_item_id=item.id,
        relevance_score=relevance_score,
        reason=reason,
    )
    session.add(link)
    session.flush()
    return link, True


def alerts_for_enrichment(
    session: Session,
    *,
    limit: int = 100,
    status: AlertStatus | None = AlertStatus.NEW,
) -> list[Alert]:
    stmt = select(Alert).where(Alert.ticker_snapshot.is_not(None)).order_by(Alert.matched_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    return list(session.execute(stmt).scalars().all())


def _candidate_sort_datetime(candidate: EnrichmentCandidate) -> datetime:
    if candidate.filed_at is not None:
        value = candidate.filed_at
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if candidate.event_date is not None:
        return datetime.combine(candidate.event_date, time.min, tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def relevant_candidates_for_alert(
    alert: Alert,
    candidates: list[EnrichmentCandidate],
    *,
    min_relevance_score: float,
    max_items: int,
) -> tuple[list[ScoredEnrichmentCandidate], int, int]:
    scored: list[ScoredEnrichmentCandidate] = []
    below_threshold = 0
    for candidate in candidates:
        relevance = relevance_for_alert(alert, candidate)
        if relevance is None:
            below_threshold += 1
            continue
        if relevance.score < min_relevance_score:
            below_threshold += 1
            continue
        scored.append(ScoredEnrichmentCandidate(candidate=candidate, relevance=relevance))

    scored.sort(
        key=lambda item: (
            item.relevance.score,
            _candidate_sort_datetime(item.candidate),
            item.candidate.external_id,
        ),
        reverse=True,
    )
    if max_items < 0 or len(scored) <= max_items:
        return scored, below_threshold, 0
    return scored[:max_items], below_threshold, len(scored) - max_items


def enrich_alerts(
    session: Session,
    *,
    source: EnrichmentSource | None = None,
    limit: int = 100,
    forms: list[str] | None = None,
    status: AlertStatus | None = AlertStatus.NEW,
    min_relevance_score: float | None = None,
    max_items_per_alert: int | None = None,
    force: bool = False,
) -> EnrichmentRunResult:
    """Best-effort enrichment pass for recently created alerts.

    This intentionally runs outside alert creation so source latency or SEC errors do
    not block alert generation.
    """

    result = EnrichmentRunResult()
    if not settings.enrichment_enabled and not force:
        result.errors.append("enrichment_disabled")
        return result

    source = source or SecEdgarSource()
    min_relevance_score = (
        settings.enrichment_min_relevance_score if min_relevance_score is None else min_relevance_score
    )
    max_items_per_alert = (
        settings.enrichment_max_items_per_alert if max_items_per_alert is None else max_items_per_alert
    )
    alerts = alerts_for_enrichment(session, limit=limit, status=status)
    result.alerts_considered = len(alerts)
    candidates_by_ticker: dict[str, list[EnrichmentCandidate]] = {}

    for alert in alerts:
        ticker = alert.ticker_snapshot.strip().upper() if alert.ticker_snapshot else None
        if not ticker:
            result.alerts_without_ticker += 1
            continue
        if ticker not in candidates_by_ticker:
            try:
                candidates_by_ticker[ticker] = source.fetch_for_ticker(ticker, forms=forms)
            except SecEdgarError as exc:
                result.errors.append(f"{ticker}: {exc.reason}:{exc.status_code}")
                candidates_by_ticker[ticker] = []
            except Exception as exc:  # pragma: no cover - defensive summary for manual runs
                result.errors.append(f"{ticker}: {exc}")
                candidates_by_ticker[ticker] = []

        candidates = candidates_by_ticker[ticker]
        result.source_items_seen += len(candidates)
        scored_candidates, below_threshold, capped_items = relevant_candidates_for_alert(
            alert,
            candidates,
            min_relevance_score=min_relevance_score,
            max_items=max_items_per_alert,
        )
        result.below_relevance_threshold += below_threshold
        result.capped_items += capped_items

        for scored_candidate in scored_candidates:
            item, created = upsert_enrichment_item(session, scored_candidate.candidate)
            if created:
                result.items_created += 1
            _, link_created = link_alert_enrichment(
                session,
                alert=alert,
                item=item,
                relevance_score=scored_candidate.relevance.score,
                reason=scored_candidate.relevance.reason,
            )
            if link_created:
                result.links_created += 1
            else:
                result.links_existing += 1

    session.commit()
    return result
