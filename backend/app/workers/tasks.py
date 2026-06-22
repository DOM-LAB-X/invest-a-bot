from __future__ import annotations

from app.db.session import SessionLocal
from app.services.pipeline import (
    run_alert_pipeline_once,
    run_daily_digest_pipeline_once,
    run_enrichment_pipeline_once,
    run_house_ingestion_once,
)
from app.workers.celery_app import celery_app


@celery_app.task(name="poll_house_disclosures")
def poll_house_disclosures() -> dict:
    with SessionLocal() as session:
        return run_house_ingestion_once(session)


@celery_app.task(name="run_alert_pipeline")
def run_alert_pipeline() -> dict:
    with SessionLocal() as session:
        return run_alert_pipeline_once(session)


@celery_app.task(name="run_enrichment_pipeline")
def run_enrichment_pipeline() -> dict:
    with SessionLocal() as session:
        return run_enrichment_pipeline_once(session)


@celery_app.task(name="run_daily_digest_pipeline")
def run_daily_digest_pipeline() -> dict:
    with SessionLocal() as session:
        return run_daily_digest_pipeline_once(session)

