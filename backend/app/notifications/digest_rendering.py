from __future__ import annotations

from app.db.models import DailyDigest, DigestScope
from app.notifications.base import NotificationMessage
from app.notifications.rendering import DISCLAIMER


def _ticker_lines(ticker_summaries: dict, limit: int = 8) -> list[str]:
    lines: list[str] = []
    sorted_items = sorted(ticker_summaries.items(), key=lambda item: item[1].get("count", 0), reverse=True)
    for ticker, summary in sorted_items[:limit]:
        lines.append(
            f"- {ticker}: {summary.get('count', 0)} trades, "
            f"buys ${summary.get('purchase_amount_min_total', '0')}, "
            f"sells ${summary.get('sale_amount_min_total', '0')}, "
            f"net ${summary.get('net_purchase_amount_min', '0')}"
        )
    return lines


def _top_alert_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for alert in payload.get("top_alerts", []):
        lines.append(
            f"- {alert.get('ticker') or alert.get('asset_name')}: "
            f"{alert.get('transaction_type')} by {alert.get('filer_name')} "
            f"(score {alert.get('score')})"
        )
    return lines


def _top_disclosure_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for disclosure in payload.get("top_disclosures", []):
        lines.append(
            f"- {disclosure.get('ticker') or disclosure.get('asset_name')}: "
            f"{disclosure.get('transaction_type')} by {disclosure.get('filer_name')} "
            f"(importance {disclosure.get('importance_score')})"
        )
    return lines


def digest_subject(digest: DailyDigest) -> str:
    payload = digest.payload or {}
    if digest.scope == DigestScope.PROFILE:
        profile_name = (payload.get("profile") or {}).get("name") or f"profile {digest.profile_id}"
        return f"Daily congressional trade digest: {profile_name} ({digest.digest_date})"
    return f"Daily congressional trade digest: all new disclosures ({digest.digest_date})"


def render_daily_digest_text(digest: DailyDigest) -> str:
    payload = digest.payload or {}
    lines = [
        digest_subject(digest),
        "",
        f"Date: {digest.digest_date} ({digest.timezone})",
    ]
    if digest.scope == DigestScope.PROFILE:
        lines.append(f"Alerts matched: {payload.get('alert_count', 0)}")
    else:
        lines.append(f"Clean new transactions: {payload.get('transaction_count', 0)}")

    delay_stats = payload.get("filing_delay_stats") or {}
    if delay_stats:
        lines.append(
            "Filing delay days: "
            f"min={delay_stats.get('min')}, avg={delay_stats.get('avg')}, max={delay_stats.get('max')}"
        )

    ticker_lines = _ticker_lines(payload.get("ticker_summaries") or {})
    if ticker_lines:
        lines.extend(["", "Volume by ticker:", *ticker_lines])

    top_lines = _top_alert_lines(payload) if digest.scope == DigestScope.PROFILE else _top_disclosure_lines(payload)
    if top_lines:
        heading = "Most important profile alerts:" if digest.scope == DigestScope.PROFILE else "Most important disclosures:"
        lines.extend(["", heading, *top_lines])

    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)


def render_daily_digest_message(digest: DailyDigest) -> NotificationMessage:
    return NotificationMessage(subject=digest_subject(digest), text=render_daily_digest_text(digest))
