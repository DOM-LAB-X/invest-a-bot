from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FilingDocument, Profile, ProfileRule, Transaction

HONORIFIC_RE = re.compile(r"^(?:hon\.?|mr\.?|mrs\.?|ms\.?|dr\.?)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class TransactionContext:
    transaction: Transaction
    filing_document: FilingDocument


@dataclass(frozen=True)
class MatchResult:
    profile: Profile
    rule: ProfileRule
    transaction: Transaction
    filing_document: FilingDocument
    filing_delay_days: int | None
    matched_conditions: dict[str, Any] = field(default_factory=dict)


def normalize_filer_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    previous = None
    while normalized and previous != normalized:
        previous = normalized
        normalized = HONORIFIC_RE.sub("", normalized).strip()
    return normalized or None


def normalize_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper().lstrip("$")
    return normalized or None


def normalize_string_list(values: list[str] | None) -> list[str]:
    return [value for value in (" ".join(str(item).split()) for item in values or []) if value]


def filing_delay_days(transaction: Transaction, filing_document: FilingDocument) -> int | None:
    if transaction.transaction_date is None or filing_document.filed_at is None:
        return None

    filed = filing_document.filed_at
    filed_date = filed.date() if isinstance(filed, datetime) else filed
    if not isinstance(filed_date, date):
        return None
    return (filed_date - transaction.transaction_date).days


def active_rules_query():
    return (
        select(Profile, ProfileRule)
        .join(ProfileRule, ProfileRule.profile_id == Profile.id)
        .where(Profile.is_active.is_(True), ProfileRule.is_active.is_(True))
    )


def load_active_rules(session: Session) -> list[tuple[Profile, ProfileRule]]:
    return list(session.execute(active_rules_query()).all())


def transaction_contexts_query(transaction_ids: list[int] | None = None):
    statement = select(Transaction, FilingDocument).join(
        FilingDocument, FilingDocument.id == Transaction.filing_document_id
    )
    if transaction_ids:
        statement = statement.where(Transaction.id.in_(transaction_ids))
    return statement


def load_transaction_contexts(
    session: Session,
    *,
    transaction_ids: list[int] | None = None,
) -> list[TransactionContext]:
    rows = session.execute(transaction_contexts_query(transaction_ids)).all()
    return [TransactionContext(transaction=row[0], filing_document=row[1]) for row in rows]


def rule_matches_transaction(
    rule: ProfileRule,
    transaction: Transaction,
    filing_document: FilingDocument,
) -> tuple[bool, dict[str, Any]]:
    conditions: dict[str, Any] = {}

    if transaction.needs_review and not rule.include_needs_review:
        return False, {"needs_review": "excluded"}

    parser_confidence = transaction.parser_confidence or 0.0
    if parser_confidence < rule.min_parser_confidence:
        return False, {
            "parser_confidence": parser_confidence,
            "min_parser_confidence": rule.min_parser_confidence,
        }
    conditions["parser_confidence"] = parser_confidence

    filer_bioguide_ids = normalize_string_list(rule.filer_bioguide_ids)
    if filer_bioguide_ids:
        if filing_document.filer_bioguide_id not in filer_bioguide_ids:
            return False, {"filer_bioguide_id": filing_document.filer_bioguide_id}
        conditions["filer_bioguide_id"] = filing_document.filer_bioguide_id

    filer_names = [normalize_filer_name(name) for name in normalize_string_list(rule.filer_names)]
    filer_names = [name for name in filer_names if name]
    if filer_names:
        normalized_filer = normalize_filer_name(filing_document.filer_name)
        if normalized_filer not in filer_names:
            return False, {"filer_name": normalized_filer}
        conditions["filer_name"] = normalized_filer

    chambers = [value.casefold() for value in normalize_string_list(rule.chambers)]
    if chambers:
        chamber = (filing_document.chamber or "").casefold()
        if chamber not in chambers:
            return False, {"chamber": chamber}
        conditions["chamber"] = chamber

    tickers = [normalize_symbol(value) for value in normalize_string_list(rule.tickers)]
    tickers = [ticker for ticker in tickers if ticker]
    if tickers:
        ticker = normalize_symbol(transaction.ticker)
        if ticker not in tickers:
            return False, {"ticker": ticker}
        conditions["ticker"] = ticker

    asset_keywords = [value.casefold() for value in normalize_string_list(rule.asset_keywords)]
    if asset_keywords:
        asset = (transaction.asset_name_raw or "").casefold()
        matched_keywords = [keyword for keyword in asset_keywords if keyword in asset]
        if not matched_keywords:
            return False, {"asset_keywords": asset_keywords}
        conditions["asset_keywords"] = matched_keywords

    if normalize_string_list(rule.sectors):
        return False, {"sectors": "sector matching unavailable until enrichment"}

    transaction_types = [value.casefold() for value in normalize_string_list(rule.transaction_types)]
    if transaction_types:
        transaction_type = (transaction.transaction_type or "").casefold()
        if transaction_type not in transaction_types:
            return False, {"transaction_type": transaction_type}
        conditions["transaction_type"] = transaction_type

    if rule.min_amount is not None:
        amount_min = transaction.amount_min
        if amount_min is None or Decimal(amount_min) < Decimal(rule.min_amount):
            return False, {"amount_min": amount_min, "min_amount": rule.min_amount}
        conditions["amount_min"] = str(amount_min)

    delay_days = filing_delay_days(transaction, filing_document)
    if rule.max_filing_delay_days is not None:
        if delay_days is None or delay_days > rule.max_filing_delay_days:
            return False, {
                "filing_delay_days": delay_days,
                "max_filing_delay_days": rule.max_filing_delay_days,
            }
        conditions["filing_delay_days"] = delay_days

    return True, conditions


def match_transaction_context(
    context: TransactionContext,
    active_rules: list[tuple[Profile, ProfileRule]],
) -> list[MatchResult]:
    matches: list[MatchResult] = []
    for profile, rule in active_rules:
        matched, conditions = rule_matches_transaction(rule, context.transaction, context.filing_document)
        if matched:
            matches.append(
                MatchResult(
                    profile=profile,
                    rule=rule,
                    transaction=context.transaction,
                    filing_document=context.filing_document,
                    filing_delay_days=filing_delay_days(context.transaction, context.filing_document),
                    matched_conditions=conditions,
                )
            )
    return matches


def match_transactions(
    session: Session,
    *,
    transaction_ids: list[int] | None = None,
) -> list[MatchResult]:
    active_rules = load_active_rules(session)
    matches: list[MatchResult] = []
    for context in load_transaction_contexts(session, transaction_ids=transaction_ids):
        matches.extend(match_transaction_context(context, active_rules))
    return matches
