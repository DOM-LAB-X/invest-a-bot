from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Profile, ProfileRule
from app.profiles.schemas import ProfileCreate, ProfileRuleCreate


def list_profiles(session: Session, *, active_only: bool = False) -> list[Profile]:
    stmt = select(Profile)
    if active_only:
        stmt = stmt.where(Profile.is_active.is_(True))
    return list(session.execute(stmt).scalars().all())


def get_profile(session: Session, profile_id: int) -> Profile | None:
    return session.get(Profile, profile_id)


def create_profile(session: Session, data: ProfileCreate) -> Profile:
    profile = Profile(name=data.name, description=data.description, is_active=data.is_active)
    profile.rules = [_build_rule(rule) for rule in data.rules]
    session.add(profile)
    session.flush()
    return profile


def add_rule(session: Session, profile_id: int, data: ProfileRuleCreate) -> ProfileRule | None:
    profile = get_profile(session, profile_id)
    if profile is None:
        return None
    rule = _build_rule(data)
    rule.profile_id = profile_id
    session.add(rule)
    session.flush()
    return rule


def remove_profile(session: Session, profile_id: int) -> bool:
    profile = get_profile(session, profile_id)
    if profile is None:
        return False
    session.delete(profile)
    session.flush()
    return True


def set_profile_active(session: Session, profile_id: int, is_active: bool) -> Profile | None:
    profile = get_profile(session, profile_id)
    if profile is None:
        return None
    profile.is_active = is_active
    session.flush()
    return profile


def _build_rule(data: ProfileRuleCreate) -> ProfileRule:
    return ProfileRule(
        name=data.name,
        is_active=data.is_active,
        filer_bioguide_ids=data.filer_bioguide_ids,
        filer_names=data.filer_names,
        chambers=data.chambers,
        tickers=data.tickers,
        asset_keywords=data.asset_keywords,
        sectors=data.sectors,
        transaction_types=data.transaction_types,
        min_amount=data.min_amount,
        max_filing_delay_days=data.max_filing_delay_days,
        min_parser_confidence=data.min_parser_confidence,
        include_needs_review=data.include_needs_review,
    )
