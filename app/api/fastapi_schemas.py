from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
    symbol: str
    side: str = "BUY"
    quantity: int = 1
    order_type: str = "MARKET"
    price: float = 1
    strategy_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class OrderTransitionRequest(BaseModel):
    status: str
    data: Dict[str, Any] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    candles: List[Dict[str, Any]]
    fast: int = 9
    slow: int = 21
    initial_capital: float = 100000
    quantity: int = 1
