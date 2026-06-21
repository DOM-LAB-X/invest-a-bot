from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.profiles.matching import MatchResult, normalize_filer_name, normalize_string_list, normalize_symbol


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reasons: dict[str, Any] = field(default_factory=dict)


def _add_component(components: list[dict[str, Any]], label: str, points: float, detail: Any = None) -> float:
    component: dict[str, Any] = {"label": label, "points": points}
    if detail is not None:
        component["detail"] = detail
    components.append(component)
    return points


def score_match(match: MatchResult) -> ScoreResult:
    transaction = match.transaction
    filing_document = match.filing_document
    rule = match.rule

    components: list[dict[str, Any]] = []
    caps: list[dict[str, Any]] = []
    score = _add_component(components, "base_rule_match", 50)

    rule_tickers = [normalize_symbol(ticker) for ticker in normalize_string_list(rule.tickers)]
    if normalize_symbol(transaction.ticker) in rule_tickers:
        score += _add_component(components, "exact_ticker_match", 10, transaction.ticker)

    rule_bioguide_ids = normalize_string_list(rule.filer_bioguide_ids)
    rule_names = [normalize_filer_name(name) for name in normalize_string_list(rule.filer_names)]
    if (
        filing_document.filer_bioguide_id
        and filing_document.filer_bioguide_id in rule_bioguide_ids
    ) or normalize_filer_name(filing_document.filer_name) in rule_names:
        score += _add_component(components, "exact_filer_match", 10, filing_document.filer_name)

    asset = (transaction.asset_name_raw or "").casefold()
    matched_keywords = [
        keyword
        for keyword in (value.casefold() for value in normalize_string_list(rule.asset_keywords))
        if keyword in asset
    ]
    if matched_keywords:
        score += _add_component(components, "asset_keyword_match", 5, matched_keywords)

    transaction_type = (transaction.transaction_type or "").casefold()
    if transaction_type == "purchase":
        score += _add_component(components, "purchase", 5)
    elif transaction_type == "sale":
        score += _add_component(components, "sale", 2)

    amount_min = Decimal(transaction.amount_min) if transaction.amount_min is not None else None
    if amount_min is not None:
        if amount_min >= Decimal("1000000"):
            score += _add_component(components, "amount_at_least_1000000", 15, str(amount_min))
        elif amount_min >= Decimal("100000"):
            score += _add_component(components, "amount_at_least_100000", 10, str(amount_min))
        elif amount_min >= Decimal("15000"):
            score += _add_component(components, "amount_at_least_15000", 5, str(amount_min))

    if match.filing_delay_days is not None:
        if match.filing_delay_days <= 3:
            score += _add_component(components, "fresh_disclosure_delay_lte_3_days", 15, match.filing_delay_days)
        elif match.filing_delay_days <= 10:
            score += _add_component(components, "fresh_disclosure_delay_lte_10_days", 8, match.filing_delay_days)

    parser_confidence = transaction.parser_confidence
    if parser_confidence is not None and parser_confidence < 0.95:
        score += _add_component(components, "parser_confidence_below_0_95", -10, parser_confidence)

    if transaction.needs_review:
        previous = score
        score = min(score, 50)
        caps.append({"label": "needs_review_cap", "cap": 50, "previous_score": previous})

    if transaction.transaction_date is None or transaction.amount_min is None:
        previous = score
        score = min(score, 70)
        caps.append({"label": "missing_date_or_amount_cap", "cap": 70, "previous_score": previous})

    score = max(0, min(100, score))
    return ScoreResult(
        score=float(score),
        reasons={
            "components": components,
            "caps": caps,
            "matched_conditions": match.matched_conditions,
            "final_score": float(score),
        },
    )
