from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.ingestion.house_clerk import HouseClerkAdapter
from app.services.alerting import generate_alerts_for_transactions
from app.services.daily_digest import generate_daily_digest_for_date
from app.services.digest_dispatch import DigestDispatchSummary, dispatch_daily_digest
from app.services.enrichment import enrich_alerts
from app.services.notification_dispatch import dispatch_new_alerts


def previous_digest_date(*, now: datetime | None = None, timezone_name: str | None = None) -> date:
    timezone_name = timezone_name or settings.digest_timezone
    local_now = (now or datetime.now(ZoneInfo(timezone_name))).astimezone(ZoneInfo(timezone_name))
    return local_now.date() - timedelta(days=1)


def run_house_ingestion_once(session: Session, *, year: int | None = None) -> dict:
    year = year or date.today().year
    result = HouseClerkAdapter().ingest_year(session, year)
    return {"stage": "house_ingestion", "year": year, **asdict(result)}


def run_alert_pipeline_once(session: Session) -> dict:
    alerts = generate_alerts_for_transactions(session)
    session.commit()
    dispatch_summary = dispatch_new_alerts(session)
    session.commit()
    return {
        "stage": "alert_pipeline",
        "alerts_created": len(alerts),
        "notification_dispatch": asdict(dispatch_summary),
    }


def run_enrichment_pipeline_once(session: Session) -> dict:
    result = enrich_alerts(session)
    return {"stage": "enrichment_pipeline", **asdict(result)}


def _merge_digest_dispatch_summaries(summaries: list[DigestDispatchSummary]) -> DigestDispatchSummary:
    merged = DigestDispatchSummary()
    for summary in summaries:
        merged.digests_considered += summary.digests_considered
        merged.deliveries_sent += summary.deliveries_sent
        merged.deliveries_failed += summary.deliveries_failed
        merged.deliveries_skipped_existing += summary.deliveries_skipped_existing
        merged.channels_skipped_unconfigured += summary.channels_skipped_unconfigured
    return merged


def run_daily_digest_pipeline_once(
    session: Session,
    *,
    digest_date: date | None = None,
    timezone_name: str | None = None,
) -> dict:
    timezone_name = timezone_name or settings.digest_timezone
    digest_date = digest_date or previous_digest_date(timezone_name=timezone_name)
    digests = generate_daily_digest_for_date(session, digest_date, timezone_name)
    session.commit()

    dispatch_summaries = [dispatch_daily_digest(session, digest) for digest in digests]
    session.commit()
    return {
        "stage": "daily_digest_pipeline",
        "digest_date": digest_date.isoformat(),
        "timezone": timezone_name,
        "digests_generated": len(digests),
        "digest_dispatch": asdict(_merge_digest_dispatch_summaries(dispatch_summaries)),
    }


def run_realtime_pipeline_once(session: Session, *, year: int | None = None) -> dict:
    house = run_house_ingestion_once(session, year=year)
    alerts = run_alert_pipeline_once(session)
    enrichment = run_enrichment_pipeline_once(session)
    return {
        "stage": "realtime_pipeline",
        "house_ingestion": house,
        "alert_pipeline": alerts,
        "enrichment_pipeline": enrichment,
    }

