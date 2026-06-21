from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Alert, AlertStatus
from app.profiles.matching import MatchResult, match_transactions
from app.services.scoring import ScoreResult, score_match


def existing_alert(session: Session, match: MatchResult) -> Alert | None:
    return session.execute(
        select(Alert).where(
            Alert.profile_id == match.profile.id,
            Alert.profile_rule_id == match.rule.id,
            Alert.transaction_id == match.transaction.id,
        )
    ).scalar_one_or_none()


def build_alert(match: MatchResult, score_result: ScoreResult | None = None) -> Alert:
    score_result = score_result or score_match(match)
    transaction = match.transaction
    filing_document = match.filing_document
    return Alert(
        profile_id=match.profile.id,
        profile_rule_id=match.rule.id,
        transaction_id=transaction.id,
        filing_document_id=filing_document.id,
        status=AlertStatus.NEW,
        score=score_result.score,
        reasons=score_result.reasons,
        matched_at=datetime.now(timezone.utc),
        filer_name_snapshot=filing_document.filer_name,
        ticker_snapshot=transaction.ticker,
        asset_name_snapshot=transaction.asset_name_raw,
        transaction_type_snapshot=transaction.transaction_type,
        amount_min_snapshot=transaction.amount_min,
        amount_max_snapshot=transaction.amount_max,
        transaction_date_snapshot=transaction.transaction_date,
        filed_at_snapshot=filing_document.filed_at,
        filing_delay_days_snapshot=match.filing_delay_days,
        parser_confidence_snapshot=transaction.parser_confidence,
        needs_review_snapshot=transaction.needs_review,
        source_url_snapshot=filing_document.source_url,
    )


def create_alert_for_match(session: Session, match: MatchResult) -> Alert | None:
    if existing_alert(session, match) is not None:
        return None
    alert = build_alert(match)
    session.add(alert)
    session.flush()
    return alert


def generate_alerts_for_transactions(
    session: Session,
    *,
    transaction_ids: list[int] | None = None,
) -> list[Alert]:
    alerts: list[Alert] = []
    for match in match_transactions(session, transaction_ids=transaction_ids):
        alert = create_alert_for_match(session, match)
        if alert is not None:
            alerts.append(alert)
    return alerts
