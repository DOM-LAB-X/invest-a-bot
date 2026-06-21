from __future__ import annotations

from decimal import Decimal

from app.db.models import Alert
from app.notifications.base import NotificationMessage

DISCLAIMER = "Public disclosure alert — not investment advice."


def _value_or_unknown(value: object | None) -> str:
    if value is None:
        return "unknown"
    return str(value)


def format_amount(min_amount: Decimal | None, max_amount: Decimal | None) -> str:
    if min_amount is None and max_amount is None:
        return "unknown amount"
    if min_amount is not None and max_amount is not None:
        if min_amount == max_amount:
            return f"${min_amount:,.0f}"
        return f"${min_amount:,.0f} - ${max_amount:,.0f}"
    if min_amount is not None:
        return f"at least ${min_amount:,.0f}"
    return f"up to ${max_amount:,.0f}"


def alert_title(alert: Alert) -> str:
    ticker_or_asset = alert.ticker_snapshot or alert.asset_name_snapshot or "Unknown asset"
    transaction_type = alert.transaction_type_snapshot or "transaction"
    filer = alert.filer_name_snapshot or "Unknown filer"
    return f"Congress trade alert: {ticker_or_asset} {transaction_type} by {filer}"


def top_score_reasons(alert: Alert, limit: int = 3) -> list[str]:
    reasons = alert.reasons or {}
    components = reasons.get("components") or []
    positive_components = [
        component
        for component in components
        if isinstance(component, dict) and component.get("points", 0) > 0 and component.get("label") != "base_rule_match"
    ]
    return [
        f"{component.get('label')} (+{component.get('points')})"
        for component in positive_components[:limit]
    ]


def render_alert_text(alert: Alert) -> str:
    lines = [
        alert_title(alert),
        "",
        f"Filer: {_value_or_unknown(alert.filer_name_snapshot)}",
        f"Asset: {_value_or_unknown(alert.asset_name_snapshot)}",
        f"Ticker: {_value_or_unknown(alert.ticker_snapshot)}",
        f"Type: {_value_or_unknown(alert.transaction_type_snapshot)}",
        f"Amount: {format_amount(alert.amount_min_snapshot, alert.amount_max_snapshot)}",
        f"Transaction date: {_value_or_unknown(alert.transaction_date_snapshot)}",
        f"Filing delay: {_value_or_unknown(alert.filing_delay_days_snapshot)} days",
        f"Score: {alert.score:.0f}",
    ]
    reasons = top_score_reasons(alert)
    if reasons:
        lines.append(f"Top reasons: {', '.join(reasons)}")
    if alert.source_url_snapshot:
        lines.append(f"Source: {alert.source_url_snapshot}")
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)


def render_email_subject(alert: Alert) -> str:
    return alert_title(alert)


def render_alert_message(alert: Alert) -> NotificationMessage:
    return NotificationMessage(subject=render_email_subject(alert), text=render_alert_text(alert))


def render_email_payload(alert: Alert, *, from_email: str, to_email: str) -> dict:
    message = render_alert_message(alert)
    return render_email_message_payload(message, from_email=from_email, to_email=to_email)


def render_email_message_payload(
    message: NotificationMessage,
    *,
    from_email: str,
    to_email: str,
) -> dict:
    return {"from": from_email, "to": [to_email], "subject": message.subject, "text": message.text}


def render_slack_payload(alert: Alert) -> dict:
    return render_slack_message_payload(render_alert_message(alert))


def render_slack_message_payload(message: NotificationMessage) -> dict:
    return {"text": message.text}
