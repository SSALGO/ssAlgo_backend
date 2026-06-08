import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class WebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    alert_name: str = Field(min_length=1)
    stocks: Optional[str] = None
    trigger_prices: Optional[str] = None

    @model_validator(mode="after")
    def require_trade_context(self):
        if not self.stocks and not getattr(self, "symbol", None):
            raise ValueError("Webhook payload must include stocks or symbol")
        return self


def parse_webhook_payload(raw_body: str) -> Dict[str, Any]:
    try:
        decoded = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Webhook payload must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Webhook payload must be a JSON object")
    try:
        return WebhookPayload.model_validate(decoded).model_dump(mode="python")
    except ValidationError as exc:
        raise ValueError(f"Invalid webhook payload: {exc.errors()}") from exc
