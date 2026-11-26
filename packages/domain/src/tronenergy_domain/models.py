from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from tronpy.keys import to_base58check_address


def address(value: str) -> str:
    try:
        result = to_base58check_address(value)
    except Exception as exc:
        raise ValueError("invalid TRON address") from exc
    if result != value:
        raise ValueError("use a Base58Check address")
    return result


Address = Annotated[str, AfterValidator(address)]
Positive = Annotated[int, Field(strict=True, gt=0, le=9_000_000_000_000_000)]


class Resource(StrEnum):
    ENERGY = "ENERGY"
    BANDWIDTH = "BANDWIDTH"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuoteInput(Input):
    resource_type: Resource
    target_units: Positive
    duration_seconds: Positive


class OrderInput(Input):
    quote_id: str
    receiver_address: Address


class Reason(Input):
    reason: str = Field(min_length=3, max_length=500)


class Approval(Reason):
    settlement_reference: str = Field(min_length=3, max_length=200)


class PoolInput(Input):
    address: Address


class TransferInput(Input):
    sender: Address
    receiver: Address
    amount: Positive
    kind: Literal["TRX", "TRC20"] = "TRX"
    contract_address: Address | None = None
    signatures: int = Field(default=1, ge=1, le=5, strict=True)


class Error(Exception):
    def __init__(self, code: str, message: str, status: int = 409, retryable: bool = False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


def now():
    return datetime.now(timezone.utc)


def ceil_div(a: int, b: int) -> int:
    if b <= 0:
        raise Error("INVALID_WEIGHT", "resource weight is unavailable", 503, True)
    return (a + b - 1) // b


def stake_for_units(units: int, weight: int, limit: int) -> int:
    return max(1_000_000, ceil_div(units * weight * 1_000_000, limit))


def bandwidth_burn(size: int, staked: int, free: int, price: int) -> int:
    return 0 if max(staked, free) >= size else size * price
