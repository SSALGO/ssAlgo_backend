import datetime

from app.domain.brokers.dhan import (
    DhanError,
    DhanService,
    canonicalize_dhan_credentials,
)

from .live_base import NormalizedLiveBrokerAdapter


class DhanBrokerAdapter(NormalizedLiveBrokerAdapter):
    broker_name = "dhan"

    EXCHANGE_SEGMENTS = {
        "NSE": "NSE_EQ",
        "NSECM": "NSE_EQ",
        "BSE": "BSE_EQ",
        "BSECM": "BSE_EQ",
        "NFO": "NSE_FNO",
        "NSEFO": "NSE_FNO",
        "BFO": "BSE_FNO",
        "BSEFO": "BSE_FNO",
        "MCX": "MCX_COMM",
        "MCXFO": "MCX_COMM",
        "CDS": "NSE_CURRENCY",
        "CDSFO": "NSE_CURRENCY",
    }
    ORDER_TYPES = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "STOP_LOSS",
        "STOP_LOSS": "STOP_LOSS",
        "SL-M": "STOP_LOSS_MARKET",
        "SLM": "STOP_LOSS_MARKET",
        "STOP_LOSS_MARKET": "STOP_LOSS_MARKET",
    }
    PRODUCT_TYPES = {
        "INTRADAY": "INTRADAY",
        "MIS": "INTRADAY",
        "CNC": "CNC",
        "DELIVERY": "CNC",
        "MARGIN": "MARGIN",
        "NORMAL": "MARGIN",
        "NRML": "MARGIN",
    }

    def __init__(self, *args, service_class=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.service_class = service_class or DhanService
        self.service = None

    def _load_credentials(self, credentials):
        values = self.load_credentials(credentials)
        canonical = canonicalize_dhan_credentials(values)
        if not canonical["dhanClientId"] or not canonical["accessToken"]:
            raise DhanError(
                "missing_credentials",
                "Dhan Client ID and access token are required.",
            )
        self.credentials = {**values, **canonical}
        return self.credentials

    @staticmethod
    def _error_result(action, exc, broker_order_id=None):
        return {
            "success": False,
            "broker": "dhan",
            "action": action,
            "status": "rejected",
            "message": str(exc),
            "error": exc.to_dict() if isinstance(exc, DhanError) else {
                "category": "unexpected_error",
                "message": str(exc),
                "retryable": False,
                "token_invalid": False,
            },
            "broker_order_id": str(broker_order_id) if broker_order_id else None,
        }

    def _update_error_health(self, user, exc):
        if not self.health_service:
            return
        fields = {
            "login_status": "rejected",
            "last_error": str(exc),
        }
        if isinstance(exc, DhanError) and exc.token_invalid:
            fields["token_status"] = "expired" if "expired" in str(exc).lower() else "invalid"
        self.health_service.update_health(user, self.broker_name, **fields)

    def verify_connection(self, credentials):
        values = self._load_credentials(credentials)
        try:
            self.service = self.service_class(
                values["dhanClientId"],
                values["accessToken"],
            )
            self.client = self.service
            result = self.service.verify_connection()
        except DhanError as exc:
            self._update_error_health(credentials.user, exc)
            raise

        now = datetime.datetime.now(datetime.UTC)
        if self.health_service:
            self.health_service.update_health(
                credentials.user,
                self.broker_name,
                login_status="connected",
                token_status="valid",
                token_expires_at=result.get("token_expires_at"),
                connected_at=now,
                last_verified_at=now,
                last_error="",
            )
        return {
            **result,
            "status": "connected",
        }

    def login(self, credentials):
        return self.verify_connection(credentials)

    def client_or_login(self, user):
        if self.service is None:
            self.login(
                type(
                    "Credentials",
                    (),
                    {"user": user, "broker": self.broker_name, "values": {}},
                )()
            )
        return self.service

    def _exchange_segment(self, exchange):
        value = str(exchange or "").strip().upper()
        return self.EXCHANGE_SEGMENTS.get(value, value or "NSE_FNO")

    def _order_type(self, value):
        normalized = str(value or "MARKET").strip().upper()
        if normalized not in self.ORDER_TYPES:
            raise ValueError(f"Unsupported Dhan order type: {normalized}")
        return self.ORDER_TYPES[normalized]

    def _product_type(self, value):
        normalized = str(value or "INTRADAY").strip().upper()
        if normalized not in self.PRODUCT_TYPES:
            raise ValueError(f"Unsupported Dhan product type: {normalized}")
        return self.PRODUCT_TYPES[normalized]

    @staticmethod
    def _safe_order_context(payload):
        return {
            key: payload.get(key)
            for key in (
                "correlationId",
                "transactionType",
                "exchangeSegment",
                "productType",
                "orderType",
                "validity",
                "securityId",
                "quantity",
            )
            if payload.get(key) not in (None, "")
        }

    def _order_payload(self, order):
        metadata = dict(order.metadata or {})
        security_id = (
            metadata.get("security_id")
            or metadata.get("securityId")
            or metadata.get("optiontoken")
            or metadata.get("token")
        )
        if not security_id:
            raise ValueError("Dhan order requires security_id in metadata")
        idempotency_key = str(
            metadata.get("idempotency_key")
            or metadata.get("correlationId")
            or ""
        ).strip()
        if not idempotency_key:
            raise ValueError("Dhan live order requires an idempotency key")
        return {
            "dhanClientId": self.credentials["dhanClientId"],
            "correlationId": idempotency_key[:25],
            "transactionType": str(order.side or "").strip().upper(),
            "exchangeSegment": self._exchange_segment(
                order.exchange
                or metadata.get("exchange")
                or metadata.get("exch")
            ),
            "productType": self._product_type(
                order.product_type
                or metadata.get("product_type")
            ),
            "orderType": self._order_type(order.order_type),
            "validity": str(metadata.get("validity") or "DAY").strip().upper(),
            "securityId": str(security_id),
            "quantity": int(order.quantity),
            "disclosedQuantity": int(metadata.get("disclosed_quantity") or 0),
            "price": float(order.price or 0),
            "triggerPrice": float(
                metadata.get("trigger_price")
                or metadata.get("triggerPrice")
                or 0
            ),
            "afterMarketOrder": bool(metadata.get("after_market_order") or False),
            "amoTime": str(metadata.get("amo_time") or "OPEN").strip().upper(),
            "boProfitValue": float(metadata.get("bo_profit_value") or 0),
            "boStopLossValue": float(
                metadata.get("bo_stop_loss_value")
                or metadata.get("bo_stop_loss_Value")
                or 0
            ),
        }

    def place_order(self, order):
        self.check_risk(order, mode="live")
        service = self.client_or_login(order.user)
        payload = self._order_payload(order)
        try:
            normalized = service.place_order(payload)
        except DhanError as exc:
            normalized = self._error_result("place_order", exc)
            if exc.token_invalid:
                self._update_error_health(order.user, exc)
        return self.record_order_result(
            order,
            normalized,
            raw_request=self._safe_order_context(payload),
        )

    def modify_order(self, user, order_id, values):
        service = self.client_or_login(user)
        payload = dict(values or {})
        payload["dhanClientId"] = self.credentials["dhanClientId"]
        payload["orderId"] = str(order_id)
        try:
            return service.modify_order(order_id, payload)
        except DhanError as exc:
            if exc.token_invalid:
                self._update_error_health(user, exc)
            return self._error_result("modify_order", exc, broker_order_id=order_id)

    def cancel_order(self, user, order_id):
        service = self.client_or_login(user)
        try:
            return service.cancel_order(order_id)
        except DhanError as exc:
            if exc.token_invalid:
                self._update_error_health(user, exc)
            return self._error_result("cancel_order", exc, broker_order_id=order_id)

    def get_profile(self, user):
        return self.client_or_login(user).get_profile()

    def get_funds(self, user):
        return self.client_or_login(user).get_funds()

    def get_positions(self, user):
        return self.client_or_login(user).get_positions()

    def get_holdings(self, user):
        return self.client_or_login(user).get_holdings()

    def get_orderbook(self, user):
        return self.client_or_login(user).get_orderbook()

    def positions(self, user):
        return self.get_positions(user)

    def funds(self, user):
        return self.get_funds(user)

    def quote(self, symbol, **kwargs):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "quote",
            "symbol": symbol,
            "message": "Dhan quote is not part of this execution-only integration.",
        }

    def subscribe(self, symbols, **kwargs):
        return {
            "success": False,
            "broker": self.broker_name,
            "action": "subscribe",
            "symbols": list(symbols or []),
            "message": "Dhan market feed is not enabled; use the shared market-data provider.",
        }
