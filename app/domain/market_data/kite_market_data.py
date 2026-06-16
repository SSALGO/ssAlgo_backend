import datetime
import threading

from app.core.trading_debug import trading_event


class KiteMarketDataService:
    """Central in-process cache for Kite ticks.

    The websocket runner can call on_tick_update for every Kite tick, while
    strategies read latest prices through get_latest_tick/get_latest_ltp.
    """

    def __init__(self):
        self._ticks = {}
        self._subscribed = set()
        self._lock = threading.RLock()
        self._ticker = None
        self._connected = False
        self._connection_key = None

    def connect(self, api_key, access_token, *, threaded=True):
        connection_key = f"{api_key}:{str(access_token or '')[:8]}"
        with self._lock:
            if self._ticker and self._connected and self._connection_key == connection_key:
                return {"connected": True, "threaded": threaded, "reused": True}
        try:
            from kiteconnect import KiteTicker
        except ImportError as exc:
            raise RuntimeError(
                "kiteconnect is required for Kite WebSocket market data. "
                "Install the official Kite Connect Python SDK on the worker."
            ) from exc

        ticker = KiteTicker(api_key, access_token)

        def on_ticks(_ws, ticks):
            for tick in ticks or []:
                self.on_tick_update(tick)

        def on_connect(ws, _response):
            with self._lock:
                tokens = sorted(self._subscribed)
                self._connected = True
            if tokens:
                ws.subscribe(tokens)
            trading_event("kite_market_data_connected", broker="zerodha", instrument_tokens=tokens, force=True)

        def on_close(_ws, code, reason):
            with self._lock:
                self._connected = False
            trading_event("kite_market_data_disconnected", broker="zerodha", code=code, reason=reason, force=True)

        ticker.on_ticks = on_ticks
        ticker.on_connect = on_connect
        ticker.on_close = on_close
        self._ticker = ticker
        self._connection_key = connection_key
        ticker.connect(threaded=threaded)
        return {"connected": True, "threaded": threaded}

    def subscribe_instruments(self, instrument_tokens):
        with self._lock:
            tokens = {int(token) for token in instrument_tokens if str(token).strip()}
            self._subscribed.update(tokens)
            ticker = self._ticker
            connected = self._connected
        if ticker and connected and tokens:
            ticker.subscribe(sorted(tokens))
        trading_event("kite_market_data_subscribed", broker="zerodha", instrument_tokens=sorted(tokens))
        return sorted(tokens)

    def unsubscribe_instruments(self, instrument_tokens):
        with self._lock:
            tokens = {int(token) for token in instrument_tokens if str(token).strip()}
            self._subscribed.difference_update(tokens)
            ticker = self._ticker
            connected = self._connected
        if ticker and connected and tokens:
            ticker.unsubscribe(sorted(tokens))
        trading_event("kite_market_data_unsubscribed", broker="zerodha", instrument_tokens=sorted(tokens))
        return sorted(tokens)

    def disconnect(self):
        ticker = self._ticker
        self._ticker = None
        with self._lock:
            self._connected = False
            self._connection_key = None
        if ticker:
            ticker.close()
        trading_event("kite_market_data_disconnected", broker="zerodha", reason="manual", force=True)

    def on_tick_update(self, tick):
        token = tick.get("instrument_token") if isinstance(tick, dict) else None
        if token is None:
            return None
        row = {
            "instrument_token": int(token),
            "last_price": tick.get("last_price"),
            "volume": tick.get("volume") or tick.get("volume_traded"),
            "ohlc": tick.get("ohlc") or {},
            "change": tick.get("change"),
            "timestamp": tick.get("timestamp") or datetime.datetime.utcnow(),
            "depth": tick.get("depth"),
        }
        with self._lock:
            self._ticks[int(token)] = row
        return row

    def get_latest_tick(self, instrument_token):
        with self._lock:
            tick = self._ticks.get(int(instrument_token))
            return dict(tick) if tick else None

    def get_latest_ltp(self, instrument_token):
        tick = self.get_latest_tick(instrument_token)
        if not tick:
            return None
        return tick.get("last_price")


kite_market_data = KiteMarketDataService()
