import os

from app.core.config import AppConfig
from app.core.secrets import decrypt_secret
from app.core.trading_debug import trading_event
from app.domain.brokers.kite import KiteService
from app.domain.market_data.kite_market_data import kite_market_data


class FeedProvider:
    name = ""

    def __init__(self, db, prices):
        self.db = db
        self.prices = prices
        self.connected = False
        self.last_error = ""

    def connect(self):
        raise NotImplementedError

    def subscribe(self, symbols):
        raise NotImplementedError

    def disconnect(self):
        self.connected = False

    def health(self):
        return {
            "provider": self.name,
            "connected": self.connected,
            "status": "connected" if self.connected else "disconnected",
            "last_error": self.last_error,
        }


class UpstoxFeedProvider(FeedProvider):
    name = "upstox"

    def connect(self):
        token = str(AppConfig.UPSTOX_ACCESS_TOKEN or os.getenv("SSLAGO_UPSTOX_ACCESS_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("Upstox live feed token is not configured")
        try:
            import upstox_client  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("upstox_client is required for Upstox live market feed") from exc
        raise RuntimeError("Upstox live websocket provider is not wired for this SDK version yet")

    def subscribe(self, symbols):
        return {
            "provider": self.name,
            "symbols": sorted(symbols or []),
            "instrument_tokens": [],
            "missing_symbols": sorted(symbols or []),
        }


class AliceBlueFeedProvider(FeedProvider):
    name = "aliceblue"

    def __init__(self, db, prices):
        super().__init__(db, prices)
        self.client = None

    def _feed_row(self):
        configured_user = str(AppConfig.ALICEBLUE_MARKET_FEED_USER or "").strip()
        if configured_user:
            return self.db["apis"].find_one({"user": configured_user, "broker": "aliceblue"}) or {}
        return self.db["apis"].find_one({"broker": "aliceblue"}) or {}

    def _secret_value(self, row, *names):
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return decrypt_secret(value)
        return ""

    def connect(self):
        row = self._feed_row()
        user_id = str(row.get("apikey") or row.get("user_id") or "").strip()
        secret_key = str(row.get("apisecret") or AppConfig.ALICEBLUE_APP_SECRET or "").strip()
        auth_code = self._secret_value(row, "auth_code")
        session_id = (
            str(AppConfig.ALICEBLUE_MARKET_FEED_SESSION_ID or "").strip()
            or self._secret_value(row, "sessionID", "user_session")
        )
        if not user_id or not secret_key:
            raise RuntimeError("AliceBlue market feed credentials are missing")
        if not session_id and not auth_code:
            raise RuntimeError("AliceBlue market feed session/auth code is missing")

        from connectors.aliceblue_adapter import AliceBlueTradeHubAdapter

        self.client = AliceBlueTradeHubAdapter(
            user_id=user_id,
            auth_code=auth_code,
            secret_key=secret_key,
            session_id=session_id or None,
        )
        self.client.get_session_id(session_id=session_id or None)
        self.client.start_websocket(
            socket_open_callback=self._mark_open,
            socket_close_callback=self._mark_closed,
            socket_error_callback=self._mark_error,
            subscription_callback=self.on_tick,
            run_in_background=True,
            market_depth=True,
        )
        self.connected = True
        return {"connected": True, "provider": self.name}

    def _mark_open(self):
        self.connected = True
        self.last_error = ""

    def _mark_closed(self):
        self.connected = False

    def _mark_error(self, error):
        self.last_error = str(error)

    def subscribe(self, symbols):
        subscribed = sorted(symbols or [])
        if self.client and hasattr(self.client, "subscribe") and subscribed:
            self.client.subscribe(subscribed)
        return {
            "provider": self.name,
            "symbols": subscribed,
            "instrument_tokens": subscribed,
            "missing_symbols": [],
        }

    def disconnect(self):
        if self.client:
            for method_name in ("stop_websocket", "close_websocket", "disconnect"):
                method = getattr(self.client, method_name, None)
                if callable(method):
                    method()
                    break
        super().disconnect()

    def on_tick(self, message):
        payload = message if isinstance(message, dict) else {}
        symbol = str(
            payload.get("symbol")
            or payload.get("ts")
            or payload.get("trading_symbol")
            or payload.get("tk")
            or ""
        ).strip().upper()
        token = payload.get("tk") or payload.get("token")
        ltp = payload.get("lp") or payload.get("ltp") or payload.get("last_price")
        bid = payload.get("bp1") or payload.get("bid") or payload.get("best_bid")
        ask = payload.get("sp1") or payload.get("ask") or payload.get("best_ask")
        row = self.prices.save_price(
            symbol=symbol or token,
            provider=self.name,
            exchange=payload.get("e") or payload.get("exchange") or "",
            token=token,
            ltp=ltp,
            bid=bid,
            ask=ask,
            depth=payload,
        )
        trading_event("market_feed_tick_saved", provider=self.name, symbol=symbol, token=token, ltp=ltp)
        return row


class ZerodhaFeedProvider(FeedProvider):
    name = "zerodha"

    def __init__(self, db, prices, kite_service=None):
        super().__init__(db, prices)
        self.kite_service = kite_service or KiteService(db)
        self._token_symbols = {}

    def _feed_user(self):
        configured = str(AppConfig.MARKET_FEED_USER or os.getenv("SSLAGO_MARKET_FEED_USER", "")).strip()
        if configured:
            return configured
        row = self.db["apis"].find_one({"broker": {"$in": ["zerodha", "kite"]}}) if self.db is not None else None
        return str((row or {}).get("user") or "").strip()

    def _access_token(self):
        env_token = str(AppConfig.MARKET_FEED_ACCESS_TOKEN or os.getenv("SSLAGO_MARKET_FEED_ACCESS_TOKEN", "")).strip()
        if env_token:
            return env_token
        feed_user = self._feed_user()
        if not feed_user:
            raise RuntimeError("No Zerodha market feed user configured")
        return self.kite_service.access_token(feed_user)

    def _instrument_rows(self, symbols):
        rows = []
        missing = []
        for symbol in sorted({str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()}):
            row = (
                self.db["kite_instruments"].find_one({"tradingsymbol": symbol})
                or self.db["kite_instruments"].find_one({"tradingsymbol": symbol, "exchange": "NFO"})
                or self.db["kite_instruments"].find_one({"tradingsymbol": symbol, "exchange": "NSE"})
            )
            if row and row.get("instrument_token"):
                rows.append(row)
            else:
                missing.append(symbol)
        return rows, missing

    def connect(self):
        kite_market_data.set_tick_callback(self.on_tick)
        result = kite_market_data.connect(
            self.kite_service.api_key,
            self._access_token(),
            threaded=True,
        )
        self.connected = True
        return result

    def subscribe(self, symbols):
        rows, missing_symbols = self._instrument_rows(symbols)
        tokens = sorted({int(row["instrument_token"]) for row in rows})
        self._token_symbols = {
            int(row["instrument_token"]): {
                "symbol": str(row.get("tradingsymbol") or "").strip().upper(),
                "exchange": str(row.get("exchange") or "").strip().upper(),
            }
            for row in rows
        }
        subscribed = kite_market_data.subscribe_instruments(tokens) if tokens else []
        return {
            "provider": self.name,
            "symbols": sorted(symbols or []),
            "instrument_tokens": subscribed,
            "missing_symbols": missing_symbols,
        }

    def disconnect(self):
        kite_market_data.disconnect()
        super().disconnect()

    def on_tick(self, tick):
        token = tick.get("instrument_token") if isinstance(tick, dict) else None
        if token is None:
            return None
        token = int(token)
        metadata = self._token_symbols.get(token) or {}
        if not metadata and self.db is not None:
            row = self.db["kite_instruments"].find_one({"instrument_token": token}) or {}
            metadata = {
                "symbol": str(row.get("tradingsymbol") or token).strip().upper(),
                "exchange": str(row.get("exchange") or "").strip().upper(),
            }
        depth = tick.get("depth") or {}
        buy = depth.get("buy") if isinstance(depth, dict) else None
        sell = depth.get("sell") if isinstance(depth, dict) else None
        bid = buy[0].get("price") if isinstance(buy, list) and buy else None
        ask = sell[0].get("price") if isinstance(sell, list) and sell else None
        row = self.prices.save_price(
            symbol=metadata.get("symbol") or str(token),
            provider=self.name,
            exchange=metadata.get("exchange") or "",
            token=token,
            ltp=tick.get("last_price"),
            bid=bid,
            ask=ask,
            depth=depth,
            received_at=tick.get("timestamp"),
        )
        trading_event(
            "market_feed_tick_saved",
            provider=self.name,
            symbol=row.get("symbol") if row else None,
            token=token,
            ltp=row.get("ltp") if row else None,
        )
        return row


PROVIDER_CLASSES = {
    "upstox": UpstoxFeedProvider,
    "aliceblue": AliceBlueFeedProvider,
    "zerodha": ZerodhaFeedProvider,
    "kite": ZerodhaFeedProvider,
}
