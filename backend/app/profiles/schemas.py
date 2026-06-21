from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ProfileRuleCreate(BaseModel):
    name: str | None = None
    is_active: bool = True
    filer_bioguide_ids: list[str] | None = None
    filer_names: list[str] | None = None
    chambers: list[str] | None = None
    tickers: list[str] | None = None
    asset_keywords: list[str] | None = None
    sectors: list[str] | None = None
    transaction_types: list[str] | None = None
    min_amount: Decimal | None = None
    max_filing_delay_days: int | None = None
    min_parser_confidence: float = 0.85
    include_needs_review: bool = False


class ProfileRuleRead(ProfileRuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int


class ProfileCreate(BaseModel):
    name: str
    description: str | None = None
    is_active: bool = True
    rules: list[ProfileRuleCreate] = []


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    rules: list[ProfileRuleRead] = []
