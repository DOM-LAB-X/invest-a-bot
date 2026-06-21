from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.db.models import Alert, EnrichmentItem
from app.enrichment.base import EnrichmentCandidate


@dataclass(frozen=True)
class EnrichmentRelevance:
    score: float
    reason: str


def _normalize_ticker(ticker: str | None) -> str | None:
    if not ticker:
        return None
    return ticker.strip().upper().replace(".", "-") or None


def _item_ticker(item: EnrichmentCandidate | EnrichmentItem) -> str | None:
    return _normalize_ticker(item.ticker)


def _item_form(item: EnrichmentCandidate | EnrichmentItem) -> str:
    return str(item.form_type or "").upper()


def _item_date(item: EnrichmentCandidate | EnrichmentItem) -> date | None:
    filed_at = item.filed_at
    if isinstance(filed_at, datetime):
        return filed_at.date()
    return item.event_date


def _alert_reference_date(alert: Alert) -> date:
    matched_at = alert.matched_at
    if matched_at is None:
        return datetime.now(timezone.utc).date()
    if matched_at.tzinfo is None:
        matched_at = matched_at.replace(tzinfo=timezone.utc)
    return matched_at.astimezone(timezone.utc).date()


def relevance_for_alert(alert: Alert, item: EnrichmentCandidate | EnrichmentItem) -> EnrichmentRelevance | None:
    """Conservatively score whether an enrichment item is relevant to an alert."""

    alert_ticker = _normalize_ticker(alert.ticker_snapshot)
    item_ticker = _item_ticker(item)
    if not alert_ticker or not item_ticker or alert_ticker != item_ticker:
        return None

    form = _item_form(item)
    base_scores = {
        "8-K": 90.0,
        "10-Q": 78.0,
        "10-K": 74.0,
        "4": 62.0,
        "S-1": 65.0,
        "424B2": 56.0,
        "424B5": 56.0,
        "SC 13D": 52.0,
        "SC 13G": 48.0,
    }
    score = base_scores.get(form, 35.0)
    reasons = [f"same ticker {alert_ticker}"]

    if form == "8-K":
        reasons.append("recent issuer 8-K can indicate material corporate events")
    elif form in {"10-Q", "10-K"}:
        reasons.append(f"recent issuer {form} provides financial context")
    elif form == "4":
        reasons.append("same-issuer Form 4 can indicate insider trading context")
    elif form:
        reasons.append(f"same-issuer SEC form {form}")
    else:
        reasons.append("same-issuer SEC filing")

    item_date = _item_date(item)
    if item_date is not None:
        age_days = (_alert_reference_date(alert) - item_date).days
        if age_days < 0:
            score -= 5
            reasons.append("filing date is after alert date")
        elif age_days <= 7:
            score += 5
            reasons.append("filed within 7 days of alert")
        elif age_days <= 30:
            reasons.append("filed within 30 days of alert")
        elif age_days <= 90:
            score -= 15
            reasons.append("filed within 90 days of alert")
        else:
            score -= 35
            reasons.append("older than 90 days")
    else:
        score -= 10
        reasons.append("filing date unavailable")

    confidence = item.confidence
    if confidence is not None and confidence < 0.8:
        score -= 10
        reasons.append("lower source confidence")

    score = max(0.0, min(100.0, round(score, 2)))
    if score <= 0:
        return None
    return EnrichmentRelevance(score=score, reason="; ".join(reasons))

