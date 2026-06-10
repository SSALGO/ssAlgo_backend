from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None
    token: Optional[str] = None
    username: Optional[str] = None
    access_token: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    mobile: str = ""


class BrokerCredentialsRequest(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)
    activate: bool = True


class PaperOrderRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: str = "BUY"
    quantity: int = Field(default=1, ge=1)
    order_type: str = "MARKET"
    price: float = Field(default=1, gt=0)
    strategy_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol", "side", "order_type", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return str(value or "").strip().upper()

    @field_validator("side")
    @classmethod
    def validate_side(cls, value):
        if value not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return value

    @field_validator("order_type")
    @classmethod
    def validate_order_type(cls, value):
        if value not in {"MARKET", "LIMIT", "SL", "SL-M"}:
            raise ValueError("unsupported order type")
        return value


class WorkerOrderRequest(PaperOrderRequest):
    user: str = Field(min_length=1)
    broker: str = ""
    mode: str = "paper"
    exchange: str = ""
    product_type: str = "INTRADAY"
    idempotency_key: Optional[str] = None

    @field_validator("user", "broker", "mode", "exchange", "product_type", mode="before")
    @classmethod
    def normalize_worker_text(cls, value):
        return str(value or "").strip()

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value):
        value = value.lower()
        if value not in {"paper", "live"}:
            raise ValueError("mode must be paper or live")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def validate_live_idempotency(cls, value, info):
        mode = str(info.data.get("mode") or "paper").lower()
        if mode == "live" and not str(value or "").strip():
            raise ValueError("idempotency_key is required for live orders")
        return value


class OrderTransitionRequest(BaseModel):
    status: str
    data: Dict[str, Any] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    candles: List[Dict[str, Any]]
    fast: int = 9
    slow: int = 21
    initial_capital: float = 100000
    quantity: int = 1
