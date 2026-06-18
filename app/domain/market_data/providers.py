import os
import threading

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

    INDEX_INSTRUMENTS = {
        "SENSEX": "BSE_INDEX|SENSEX",
        "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank",
        "MIDCPNIFTY": "NSE_INDEX|NIFTY MIDCAP 150",
        "NIFTY": "NSE_INDEX|Nifty 50",
    }

    def __init__(self, db, prices):
        super().__init__(db, prices)
        self.client = None
        self.streamer = None
        self._thread = None
        self._instrument_symbols = {}
        self._subscribed_keys = []
        self._open_event = threading.Event()

    def _access_token(self):
        return str(
            AppConfig.UPSTOX_ACCESS_TOKEN
            or AppConfig.MARKET_FEED_ACCESS_TOKEN
            or os.getenv("SSLAGO_UPSTOX_ACCESS_TOKEN", "")
            or os.getenv("SSLAGO_MARKET_FEED_ACCESS_TOKEN", "")
        ).strip()

    def _env_instruments(self):
        mappings = {}
        raw = os.getenv("SSLAGO_UPSTOX_INSTRUMENTS", "")
        for item in raw.split(","):
            if "=" not in item:
                continue
            symbol, instrument_key = item.split("=", 1)
            symbol = symbol.strip().upper()
            instrument_key = instrument_key.strip()
            if symbol and instrument_key:
                mappings[symbol] = instrument_key
        return mappings

    def _instrument_key(self, symbol):
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return ""
        env_match = self._env_instruments().get(symbol)
        if env_match:
            return env_match
        if symbol in self.INDEX_INSTRUMENTS:
            return self.INDEX_INSTRUMENTS[symbol]
        if self.db is not None:
            row = (
                self.db["upstox_instruments"].find_one({"tradingsymbol": symbol})
                or self.db["upstox_instruments"].find_one({"symbol": symbol})
                or self.db["upstox_instruments"].find_one({"name": symbol})
            )
            if row and row.get("instrument_key"):
                return str(row["instrument_key"]).strip()
        return symbol

    def connect(self):
        token = self._access_token()
        if not token:
            raise RuntimeError("Upstox live feed token is not configured")
        try:
            import upstox_client
        except ImportError as exc:
            raise RuntimeError("upstox_client is required for Upstox live market feed") from exc

        configuration = upstox_client.Configuration()
        configuration.access_token = token
        self.client = upstox_client.ApiClient(configuration)
        streamer_class = getattr(
            upstox_client,
            "MarketDataStreamerV3",
            getattr(upstox_client, "MarketDataStreamer", None),
        )
        if streamer_class is None:
            raise RuntimeError("Installed upstox_client does not expose a market data streamer")

        try:
            self.streamer = streamer_class(self.client, list(self._subscribed_keys), "full")
        except TypeError:
            self.streamer = streamer_class(self.client)

        self._register_callbacks(self.streamer)
        self.last_error = ""
        self._open_event.clear()

        self._thread = threading.Thread(
            target=self._run_streamer,
            name="upstox-market-feed",
            daemon=True,
        )
        self._thread.start()
        timeout = float(os.getenv("SSLAGO_MARKET_FEED_CONNECT_TIMEOUT_SECONDS", "10"))
        if not self._open_event.wait(timeout):
            self.connected = False
            if self.last_error:
                raise RuntimeError(f"Upstox live market feed failed: {self.last_error}")
            raise RuntimeError("Upstox live market feed websocket did not open")
        return {"connected": True, "provider": self.name, "threaded": True}

    def _register_callbacks(self, streamer):
        on = getattr(streamer, "on", None)
        if not callable(on):
            return
        for event_name, callback in (
            ("open", self._mark_open),
            ("message", self.on_tick),
            ("error", self._mark_error),
            ("close", self._mark_closed),
        ):
            try:
                on(event_name, callback)
            except Exception:
                pass

    def _run_streamer(self):
        try:
            self.streamer.connect()
        except Exception as exc:
            self._mark_error(exc)

    def _mark_open(self, *_args, **_kwargs):
        self.connected = True
        self.last_error = ""
        self._open_event.set()
        if self._subscribed_keys:
            self._subscribe_streamer(self._subscribed_keys)

    def _mark_closed(self, *_args, **_kwargs):
        self.connected = False
        self._open_event.clear()

    def _mark_error(self, error, *_args, **_kwargs):
        self.connected = False
        self.last_error = str(error)
        self._open_event.set()

    def _subscribe_streamer(self, instrument_keys):
        if not self.streamer or not instrument_keys:
            return []
        subscribe = getattr(self.streamer, "subscribe", None)
        if not callable(subscribe):
            return []
        try:
            subscribe(instrument_keys, "full")
        except TypeError:
            subscribe("full", instrument_keys)
        except Exception as exc:
            if "not open" in str(exc).lower():
                trading_event(
                    "market_feed_subscribe_deferred",
                    provider=self.name,
                    instrument_tokens=sorted(instrument_keys),
                    reason=str(exc),
                )
                return []
            raise
        return sorted(instrument_keys)

    def subscribe(self, symbols):
        subscribed = []
        missing = []
        self._instrument_symbols = {}
        for symbol in sorted(symbols or []):
            instrument_key = self._instrument_key(symbol)
            if not instrument_key:
                missing.append(symbol)
                continue
            subscribed.append(instrument_key)
            self._instrument_symbols[instrument_key] = str(symbol or "").strip().upper()
        self._subscribed_keys = sorted(set(subscribed))
        if self.streamer:
            self._subscribe_streamer(self._subscribed_keys)
        return {
            "provider": self.name,
            "symbols": sorted(symbols or []),
            "instrument_tokens": self._subscribed_keys,
            "missing_symbols": sorted(missing),
        }

    def disconnect(self):
        if self.streamer:
            for method_name in ("disconnect", "close"):
                method = getattr(self.streamer, method_name, None)
                if callable(method):
                    method()
                    break
        super().disconnect()

    def _plain(self, value):
        if isinstance(value, dict):
            return {key: self._plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._plain(item) for item in value]
        if hasattr(value, "DESCRIPTOR"):
            try:
                from google.protobuf.json_format import MessageToDict

                return self._plain(MessageToDict(value, preserving_proto_field_name=True))
            except Exception:
                pass
        if hasattr(value, "to_dict"):
            return self._plain(value.to_dict())
        if hasattr(value, "__dict__"):
            return {
                key: self._plain(item)
                for key, item in value.__dict__.items()
                if not key.startswith("_")
            }
        return value

    def _nested(self, value, *paths):
        for path in paths:
            current = value
            for key in path:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            if current not in (None, ""):
                return current
        return None

    def _extract_ltp(self, feed):
        return self._nested(
            feed,
            ("ltp",),
            ("ltpc", "ltp"),
            ("ff", "indexFF", "ltpc", "ltp"),
            ("ff", "marketFF", "ltpc", "ltp"),
            ("fullFeed", "indexFF", "ltpc", "ltp"),
            ("fullFeed", "marketFF", "ltpc", "ltp"),
        )

    def _extract_bid_ask(self, feed):
        quotes = self._nested(
            feed,
            ("marketLevel", "bidAskQuote"),
            ("ff", "marketFF", "marketLevel", "bidAskQuote"),
            ("fullFeed", "marketFF", "marketLevel", "bidAskQuote"),
        )
        if not isinstance(quotes, list) or not quotes:
            return None, None
        top = quotes[0] if isinstance(quotes[0], dict) else {}
        return top.get("bidP") or top.get("bid_price"), top.get("askP") or top.get("ask_price")

    def on_tick(self, message):
        payload = self._plain(message)
        feeds = payload.get("feeds") if isinstance(payload, dict) else None
        if not isinstance(feeds, dict):
            return None
        saved = None
        for instrument_key, feed in feeds.items():
            symbol = self._instrument_symbols.get(instrument_key) or str(instrument_key or "").strip().upper()
            ltp = self._extract_ltp(feed)
            bid, ask = self._extract_bid_ask(feed)
            saved = self.prices.save_price(
                symbol=symbol,
                provider=self.name,
                exchange=str(instrument_key).split("|", 1)[0],
                token=instrument_key,
                ltp=ltp,
                bid=bid,
                ask=ask,
                depth=feed,
            )
            trading_event("market_feed_tick_saved", provider=self.name, symbol=symbol, token=instrument_key, ltp=ltp)
        return saved


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
