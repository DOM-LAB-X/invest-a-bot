from __future__ import annotations

from app.api.routes_alerts import dismiss_alert, mark_alert_read
from app.db.models import Alert, AlertStatus


class _FakeSession:
    def __init__(self, alert):
        self.alert = alert
        self.committed = False
        self.refreshed = None

    def get(self, model, alert_id):
        if model is Alert and self.alert.id == alert_id:
            return self.alert
        return None

    def commit(self):
        self.committed = True

    def refresh(self, value):
        self.refreshed = value


def test_mark_alert_read_updates_status() -> None:
    alert = Alert(id=1, profile_id=1, profile_rule_id=1, transaction_id=1, filing_document_id=1, score=50)
    session = _FakeSession(alert)

    result = mark_alert_read(1, session)

    assert result.status == AlertStatus.READ
    assert session.committed is True
    assert session.refreshed is alert


def test_dismiss_alert_updates_status() -> None:
    alert = Alert(id=1, profile_id=1, profile_rule_id=1, transaction_id=1, filing_document_id=1, score=50)
    session = _FakeSession(alert)

    result = dismiss_alert(1, session)

    assert result.status == AlertStatus.DISMISSED
    assert session.committed is True
    assert session.refreshed is alert
