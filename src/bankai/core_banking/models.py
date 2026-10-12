from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class AccountType(StrEnum):
    SAVINGS = "savings"
    CURRENT = "current"
    FIXED_DEPOSIT = "fixed_deposit"


class Account(BaseModel):
    account_id: str
    account_number: str
    type: AccountType
    currency: str = "LKR"
    nickname: str | None = None
    status: str = "active"


class Balance(BaseModel):
    account_id: str
    currency: str
    available: Decimal
    ledger: Decimal
    as_of: datetime


class Transaction(BaseModel):
    transaction_id: str
    account_id: str
    booked_at: datetime
    amount: Decimal  # negative = debit
    currency: str
    description: str  # free text from counterparties: UNTRUSTED
    category: str | None = None
    channel: str | None = None
    status: str = "posted"
    reference: str | None = None


class Address(BaseModel):
    line1: str = Field(min_length=3, max_length=120)
    line2: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=60)
    postal_code: str = Field(min_length=3, max_length=12)
    country: str = Field(default="LK", min_length=2, max_length=2)

    @field_validator("country")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class Profile(BaseModel):
    customer_id: str
    full_name: str
    email: str
    mobile: str
    address: Address


class StatementRequest(BaseModel):
    account_id: str
    from_date: date
    to_date: date
    delivery: str = Field(default="email", pattern="^(email|branch|post)$")

    @field_validator("to_date")
    @classmethod
    def _range(cls, v: date, info) -> date:  # noqa: ANN001
        start = info.data.get("from_date")
        if start and v < start:
            raise ValueError("to_date must be on or after from_date")
        if start and (v - start).days > 366:
            raise ValueError("statement period cannot exceed 12 months")
        return v


class ChequeBookRequest(BaseModel):
    account_id: str
    leaves: int = Field(default=25, ge=10, le=100)
    delivery: str = Field(default="branch", pattern="^(branch|post)$")


class KycStatus(BaseModel):
    customer_id: str
    status: str
    last_reviewed: date
    next_review_due: date
    missing_items: list[str] = []


class KycUpdate(BaseModel):
    occupation: str | None = Field(default=None, max_length=80)
    employer: str | None = Field(default=None, max_length=120)
    annual_income_band: str | None = Field(default=None, pattern="^(<1M|1M-3M|3M-6M|6M-12M|>12M)$")
    source_of_funds: str | None = Field(default=None, max_length=120)


class ServiceRequestReceipt(BaseModel):
    request_id: str
    type: str
    status: str
    created_at: datetime
    expected_completion: date | None = None
    details: dict = {}
