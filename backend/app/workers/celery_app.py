from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "invest_a_bot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    timezone=settings.celery_timezone,
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "poll-house-disclosures": {
            "task": "poll_house_disclosures",
            "schedule": settings.house_clerk_poll_interval_seconds,
        },
        "run-alert-pipeline": {
            "task": "run_alert_pipeline",
            "schedule": settings.alert_pipeline_interval_seconds,
        },
        "run-enrichment-pipeline": {
            "task": "run_enrichment_pipeline",
            "schedule": settings.enrichment_pipeline_interval_seconds,
        },
        "run-daily-digest-pipeline": {
            "task": "run_daily_digest_pipeline",
            "schedule": crontab(
                hour=settings.daily_digest_hour,
                minute=settings.daily_digest_minute,
            ),
        },
    },
)

