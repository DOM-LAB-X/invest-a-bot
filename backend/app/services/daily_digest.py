from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.db.models import Alert, DailyDigest, DigestScope, DigestStatus, FilingDocument, Profile, Transaction

PARSER_CONFIDENCE_MINIMUM = 0.85


@dataclass(frozen=True)
class DigestWindow:
    digest_date: date
    timezone: str
    start_utc: datetime
    end_utc: datetime


def digest_window_for_date(digest_date: date, timezone_name: str) -> DigestWindow:
    local_tz = ZoneInfo(timezone_name)
    local_start = datetime.combine(digest_date, time.min, tzinfo=local_tz)
    local_end = local_start + timedelta(days=1)
    return DigestWindow(
        digest_date=digest_date,
        timezone=timezone_name,
        start_utc=local_start.astimezone(timezone.utc),
        end_utc=local_end.astimezone(timezone.utc),
    )


def _decimal_to_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _date_to_string(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _amount_min(value: Decimal | None) -> Decimal:
    return Decimal(value or 0)


def _ticker_key(ticker: str | None, asset_name: str | None) -> str:
    return ticker or asset_name or "unknown"


def _filing_delay_stats(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "max": None, "avg": None}
    return {"min": min(values), "max": max(values), "avg": sum(values) / len(values)}


def _empty_ticker_summary() -> dict[str, Any]:
    return {
        "count": 0,
        "purchase_count": 0,
        "sale_count": 0,
        "purchase_amount_min_total": "0",
        "sale_amount_min_total": "0",
        "net_purchase_amount_min": "0",
    }


def _add_trade_to_ticker_summary(
    summary: dict[str, Any],
    transaction_type: str | None,
    amount_min: Decimal | None,
) -> None:
    amount = _amount_min(amount_min)
    purchase_total = Decimal(summary["purchase_amount_min_total"])
    sale_total = Decimal(summary["sale_amount_min_total"])
    summary["count"] += 1
    if (transaction_type or "").casefold() == "purchase":
        summary["purchase_count"] += 1
        purchase_total += amount
    elif (transaction_type or "").casefold() == "sale":
        summary["sale_count"] += 1
        sale_total += amount
    summary["purchase_amount_min_total"] = str(purchase_total)
    summary["sale_amount_min_total"] = str(sale_total)
    summary["net_purchase_amount_min"] = str(purchase_total - sale_total)


def alert_summary(alert: Alert) -> dict[str, Any]:
    return {
        "alert_id": alert.id,
        "profile_id": alert.profile_id,
        "ticker": alert.ticker_snapshot,
        "asset_name": alert.asset_name_snapshot,
        "filer_name": alert.filer_name_snapshot,
        "transaction_type": alert.transaction_type_snapshot,
        "amount_min": _decimal_to_string(alert.amount_min_snapshot),
        "amount_max": _decimal_to_string(alert.amount_max_snapshot),
        "transaction_date": _date_to_string(alert.transaction_date_snapshot),
        "filing_delay_days": alert.filing_delay_days_snapshot,
        "score": alert.score,
        "source_url": alert.source_url_snapshot,
    }


def transaction_summary(transaction: Transaction, filing_document: FilingDocument, score: float) -> dict[str, Any]:
    return {
        "transaction_id": transaction.id,
        "filing_document_id": filing_document.id,
        "ticker": transaction.ticker,
        "asset_name": transaction.asset_name_raw,
        "filer_name": filing_document.filer_name,
        "transaction_type": transaction.transaction_type,
        "amount_min": _decimal_to_string(transaction.amount_min),
        "amount_max": _decimal_to_string(transaction.amount_max),
        "transaction_date": _date_to_string(transaction.transaction_date),
        "created_at": _date_to_string(transaction.created_at),
        "parser_confidence": transaction.parser_confidence,
        "needs_review": transaction.needs_review,
        "importance_score": score,
        "source_url": filing_document.source_url,
    }


def rank_transaction_importance(transaction: Transaction, filing_document: FilingDocument) -> float:
    score = 50.0
    if transaction.ticker:
        score += 5
    transaction_type = (transaction.transaction_type or "").casefold()
    if transaction_type == "purchase":
        score += 5
    elif transaction_type == "sale":
        score += 2
    amount = _amount_min(transaction.amount_min)
    if amount >= Decimal("1000000"):
        score += 15
    elif amount >= Decimal("100000"):
        score += 10
    elif amount >= Decimal("15000"):
        score += 5
    if filing_document.filed_at and transaction.transaction_date:
        delay = (filing_document.filed_at.date() - transaction.transaction_date).days
        if delay <= 3:
            score += 15
        elif delay <= 10:
            score += 8
    if transaction.parser_confidence is not None and transaction.parser_confidence < 0.95:
        score -= 10
    return max(0, min(100, score))


def build_profile_digest_payload(
    *,
    profile: Profile,
    alerts: list[Alert],
    window: DigestWindow,
    top_n: int,
) -> dict[str, Any]:
    ticker_summaries: dict[str, dict[str, Any]] = defaultdict(_empty_ticker_summary)
    delays = [alert.filing_delay_days_snapshot for alert in alerts if alert.filing_delay_days_snapshot is not None]
    for alert in alerts:
        key = _ticker_key(alert.ticker_snapshot, alert.asset_name_snapshot)
        _add_trade_to_ticker_summary(
            ticker_summaries[key],
            alert.transaction_type_snapshot,
            alert.amount_min_snapshot,
        )

    top_alerts = sorted(alerts, key=lambda alert: alert.score, reverse=True)[:top_n]
    return {
        "scope": DigestScope.PROFILE.value,
        "digest_date": window.digest_date.isoformat(),
        "timezone": window.timezone,
        "profile": {"id": profile.id, "name": profile.name},
        "alert_count": len(alerts),
        "ticker_summaries": dict(ticker_summaries),
        "filing_delay_stats": _filing_delay_stats(delays),
        "top_alerts": [alert_summary(alert) for alert in top_alerts],
    }


def build_cross_profile_digest_payload(
    *,
    transactions: list[tuple[Transaction, FilingDocument]],
    window: DigestWindow,
    top_n: int,
) -> dict[str, Any]:
    ticker_summaries: dict[str, dict[str, Any]] = defaultdict(_empty_ticker_summary)
    ranked: list[tuple[float, Transaction, FilingDocument]] = []
    delays: list[int] = []
    for transaction, filing_document in transactions:
        key = _ticker_key(transaction.ticker, transaction.asset_name_raw)
        _add_trade_to_ticker_summary(ticker_summaries[key], transaction.transaction_type, transaction.amount_min)
        score = rank_transaction_importance(transaction, filing_document)
        ranked.append((score, transaction, filing_document))
        if filing_document.filed_at and transaction.transaction_date:
            delays.append((filing_document.filed_at.date() - transaction.transaction_date).days)

    top_transactions = sorted(ranked, key=lambda row: row[0], reverse=True)[:top_n]
    return {
        "scope": DigestScope.CROSS_PROFILE.value,
        "digest_date": window.digest_date.isoformat(),
        "timezone": window.timezone,
        "transaction_count": len(transactions),
        "ticker_summaries": dict(ticker_summaries),
        "filing_delay_stats": _filing_delay_stats(delays),
        "top_disclosures": [
            transaction_summary(transaction, filing_document, score)
            for score, transaction, filing_document in top_transactions
        ],
    }


def profile_alerts_for_window(session: Session, window: DigestWindow) -> dict[Profile, list[Alert]]:
    rows = session.execute(
        select(Alert, Profile)
        .join(Profile, Profile.id == Alert.profile_id)
        .where(Alert.matched_at >= window.start_utc, Alert.matched_at < window.end_utc)
        .order_by(Profile.id.asc(), Alert.score.desc())
    ).all()
    grouped: dict[Profile, list[Alert]] = defaultdict(list)
    for alert, profile in rows:
        grouped[profile].append(alert)
    return grouped


def clean_transactions_for_window(session: Session, window: DigestWindow) -> list[tuple[Transaction, FilingDocument]]:
    rows = session.execute(
        select(Transaction, FilingDocument)
        .join(FilingDocument, FilingDocument.id == Transaction.filing_document_id)
        .where(
            Transaction.created_at >= window.start_utc,
            Transaction.created_at < window.end_utc,
            Transaction.needs_review.is_(False),
            Transaction.parser_confidence >= PARSER_CONFIDENCE_MINIMUM,
        )
        .order_by(Transaction.created_at.asc())
    ).all()
    return [(transaction, filing_document) for transaction, filing_document in rows]


def existing_digest(
    session: Session,
    *,
    digest_date: date,
    timezone_name: str,
    scope: DigestScope,
    profile_id: int | None,
) -> DailyDigest | None:
    statement = select(DailyDigest).where(
        DailyDigest.digest_date == digest_date,
        DailyDigest.timezone == timezone_name,
        DailyDigest.scope == scope,
    )
    if profile_id is None:
        statement = statement.where(DailyDigest.profile_id.is_(None))
    else:
        statement = statement.where(DailyDigest.profile_id == profile_id)
    return session.execute(statement).scalar_one_or_none()


def create_or_update_digest(
    session: Session,
    *,
    digest_date: date,
    timezone_name: str,
    scope: DigestScope,
    profile_id: int | None,
    payload: dict[str, Any],
    force: bool = False,
) -> DailyDigest:
    digest = existing_digest(
        session,
        digest_date=digest_date,
        timezone_name=timezone_name,
        scope=scope,
        profile_id=profile_id,
    )
    if digest is not None and not force:
        return digest
    now = datetime.now(timezone.utc)
    if digest is None:
        digest = DailyDigest(
            digest_date=digest_date,
            timezone=timezone_name,
            scope=scope,
            profile_id=profile_id,
        )
        session.add(digest)
    digest.status = DigestStatus.GENERATED
    digest.payload = payload
    digest.generated_at = now
    digest.error = None
    session.flush()
    return digest


def generate_daily_digest_for_date(
    session: Session,
    digest_date: date,
    timezone_name: str | None = None,
    *,
    config: Settings = settings,
    force: bool = False,
) -> list[DailyDigest]:
    if not config.daily_digest_enabled:
        return []

    timezone_name = timezone_name or config.digest_timezone
    top_n = config.digest_top_n
    window = digest_window_for_date(digest_date, timezone_name)
    digests: list[DailyDigest] = []

    for profile, alerts in profile_alerts_for_window(session, window).items():
        payload = build_profile_digest_payload(profile=profile, alerts=alerts, window=window, top_n=top_n)
        digests.append(
            create_or_update_digest(
                session,
                digest_date=digest_date,
                timezone_name=timezone_name,
                scope=DigestScope.PROFILE,
                profile_id=profile.id,
                payload=payload,
                force=force,
            )
        )

    cross_profile_payload = build_cross_profile_digest_payload(
        transactions=clean_transactions_for_window(session, window),
        window=window,
        top_n=top_n,
    )
    digests.append(
        create_or_update_digest(
            session,
            digest_date=digest_date,
            timezone_name=timezone_name,
            scope=DigestScope.CROSS_PROFILE,
            profile_id=None,
            payload=cross_profile_payload,
            force=force,
        )
    )
    return digests
