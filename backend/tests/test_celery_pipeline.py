from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services import pipeline
from app.workers.celery_app import celery_app
from app.workers import tasks


def test_celery_beat_schedule_uses_settings_and_excludes_senate() -> None:
    schedule = celery_app.conf.beat_schedule

    assert schedule["poll-house-disclosures"]["task"] == "poll_house_disclosures"
    assert schedule["poll-house-disclosures"]["schedule"] == settings.house_clerk_poll_interval_seconds
    assert schedule["run-alert-pipeline"]["schedule"] == settings.alert_pipeline_interval_seconds
    assert schedule["run-enrichment-pipeline"]["schedule"] == settings.enrichment_pipeline_interval_seconds

    daily_schedule = schedule["run-daily-digest-pipeline"]["schedule"]
    assert daily_schedule._orig_hour == settings.daily_digest_hour
    assert daily_schedule._orig_minute == settings.daily_digest_minute
    assert celery_app.conf.timezone == settings.celery_timezone

    serialized = repr(schedule).casefold()
    assert "senate" not in serialized


def test_previous_digest_date_uses_previous_calendar_day_in_digest_timezone() -> None:
    now = datetime(2026, 6, 21, 8, 0, tzinfo=ZoneInfo("America/New_York"))

    assert pipeline.previous_digest_date(now=now, timezone_name="America/New_York") == date(2026, 6, 20)


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


@dataclass
class _FakeIngestionResult:
    discovered: int = 1
    stored: int = 1
    created: int = 1
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] | None = None


def test_run_house_ingestion_once_returns_serializable_summary(monkeypatch) -> None:
    class _FakeAdapter:
        def ingest_year(self, session, year):
            assert isinstance(session, _FakeSession)
            assert year == 2026
            return _FakeIngestionResult(errors=[])

    monkeypatch.setattr(pipeline, "HouseClerkAdapter", lambda: _FakeAdapter())

    result = pipeline.run_house_ingestion_once(_FakeSession(), year=2026)

    assert result == {
        "stage": "house_ingestion",
        "year": 2026,
        "discovered": 1,
        "stored": 1,
        "created": 1,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": [],
    }


def test_run_alert_pipeline_commits_after_generation_and_dispatch(monkeypatch) -> None:
    session = _FakeSession()
    calls: list[str] = []

    monkeypatch.setattr(pipeline, "generate_alerts_for_transactions", lambda _session: calls.append("generate") or [1, 2])

    @dataclass
    class _DispatchSummary:
        alerts_considered: int = 2
        deliveries_sent: int = 1
        deliveries_failed: int = 0
        deliveries_skipped_existing: int = 0
        channels_skipped_unconfigured: int = 1

    monkeypatch.setattr(pipeline, "dispatch_new_alerts", lambda _session: calls.append("dispatch") or _DispatchSummary())

    result = pipeline.run_alert_pipeline_once(session)

    assert calls == ["generate", "dispatch"]
    assert session.commits == 2
    assert result["alerts_created"] == 2
    assert result["notification_dispatch"]["deliveries_sent"] == 1


def test_run_daily_digest_pipeline_uses_previous_day_and_dispatches(monkeypatch) -> None:
    session = _FakeSession()
    digest_dates: list[date] = []

    class _Digest:
        id = 1

    def _generate(_session, digest_date, timezone_name):
        digest_dates.append(digest_date)
        assert timezone_name == "America/New_York"
        return [_Digest()]

    @dataclass
    class _DigestDispatchSummary:
        digests_considered: int = 1
        deliveries_sent: int = 1
        deliveries_failed: int = 0
        deliveries_skipped_existing: int = 0
        channels_skipped_unconfigured: int = 0

    monkeypatch.setattr(pipeline, "generate_daily_digest_for_date", _generate)
    monkeypatch.setattr(pipeline, "dispatch_daily_digest", lambda _session, _digest: _DigestDispatchSummary())
    monkeypatch.setattr(
        pipeline,
        "previous_digest_date",
        lambda timezone_name=None: date(2026, 6, 20),
    )

    result = pipeline.run_daily_digest_pipeline_once(session, timezone_name="America/New_York")

    assert digest_dates == [date(2026, 6, 20)]
    assert session.commits == 2
    assert result["digests_generated"] == 1
    assert result["digest_dispatch"]["deliveries_sent"] == 1


def test_run_realtime_pipeline_composes_ingest_alerts_and_enrichment(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_house_ingestion_once", lambda session, year=None: calls.append("house") or {})
    monkeypatch.setattr(pipeline, "run_alert_pipeline_once", lambda session: calls.append("alerts") or {})
    monkeypatch.setattr(pipeline, "run_enrichment_pipeline_once", lambda session: calls.append("enrich") or {})

    result = pipeline.run_realtime_pipeline_once(_FakeSession(), year=2026)

    assert calls == ["house", "alerts", "enrich"]
    assert result["stage"] == "realtime_pipeline"


class _SessionContext:
    def __init__(self, session):
        self.session = session
        self.exited = False

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True


def test_celery_tasks_open_their_own_session(monkeypatch) -> None:
    session = _FakeSession()
    context = _SessionContext(session)
    monkeypatch.setattr(tasks, "SessionLocal", lambda: context)
    monkeypatch.setattr(tasks, "run_alert_pipeline_once", lambda task_session: {"session": task_session is session})

    result = tasks.run_alert_pipeline.run()

    assert result == {"session": True}
    assert context.exited is True
