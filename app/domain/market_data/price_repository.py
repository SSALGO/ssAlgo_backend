import datetime
import time
from threading import RLock

from app.core.config import AppConfig


def utcnow():
    return datetime.datetime.now(datetime.UTC)


def _as_aware(value):
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    return datetime.datetime.min.replace(tzinfo=datetime.UTC)


class MarketPriceRepository:
    """Mongo-backed normalized live price cache."""

    GLOBAL_HEALTH_PROVIDER = "__global__"
    _cache_lock = RLock()
    _latest_cache = {}
    _last_write_times = {}

    def __init__(self, db, stale_seconds=None, write_interval_seconds=None):
        self.db = db
        self.stale_seconds = int(
            stale_seconds
            if stale_seconds is not None
            else AppConfig.MARKET_PRICE_STALE_SECONDS
        )
        self.write_interval_seconds = float(
            write_interval_seconds
            if write_interval_seconds is not None
            else AppConfig.MARKET_PRICE_WRITE_INTERVAL_SECONDS
        )

    @property
    def prices(self):
        return self.db["market_prices"]

    @property
    def health(self):
        return self.db["market_feed_health"]

    def _cache_key(self, row_key):
        return (
            id(self.db),
            row_key.get("provider", ""),
            row_key.get("symbol", ""),
            row_key.get("exchange", ""),
            row_key.get("token", ""),
        )

    def save_price(
        self,
        *,
        symbol,
        provider,
        exchange="",
        token=None,
        ltp=None,
        bid=None,
        ask=None,
        depth=None,
        received_at=None,
        force=False,
    ):
        now = utcnow()
        received_at = received_at or now
        symbol_text = str(symbol or "").strip().upper()
        token_text = str(token or "").strip()
        if not symbol_text and not token_text:
            return None
        row_key = {
            "provider": str(provider or "").strip().lower(),
            "symbol": symbol_text,
            "exchange": str(exchange or "").strip().upper(),
            "token": token_text,
        }
        payload = {
            **row_key,
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            "depth": depth or {},
            "received_at": received_at,
            "updated_at": now,
            "stale_after": received_at + datetime.timedelta(seconds=self.stale_seconds),
        }
        cache_key = self._cache_key(row_key)
        should_write = force
        monotonic_now = time.monotonic()
        with self._cache_lock:
            self._latest_cache[cache_key] = dict(payload)
            last_write = self._last_write_times.get(cache_key)
            if last_write is None or self.write_interval_seconds <= 0:
                should_write = True
            elif monotonic_now - last_write >= self.write_interval_seconds:
                should_write = True
            if should_write:
                self._last_write_times[cache_key] = monotonic_now
        if not should_write and self.prices.find_one(row_key) is None:
            should_write = True
            with self._cache_lock:
                self._last_write_times[cache_key] = monotonic_now
        if should_write:
            self.prices.update_one(row_key, {"$set": payload}, upsert=True)
        return payload

    def _cache_candidates(self, query):
        rows = []
        with self._cache_lock:
            for key, row in self._latest_cache.items():
                if key[0] != id(self.db):
                    continue
                if all(row.get(field) == expected for field, expected in query.items()):
                    rows.append(dict(row))
        return rows

    def latest_price(self, symbol=None, exchange=None, token=None, provider=None, require_fresh=True):
        query = {}
        if provider:
            query["provider"] = str(provider).strip().lower()
        if symbol:
            query["symbol"] = str(symbol).strip().upper()
        if exchange:
            query["exchange"] = str(exchange).strip().upper()
        if token not in (None, ""):
            query["token"] = str(token).strip()

        candidates = []
        if query:
            candidates.extend(self._cache_candidates(query))
            candidates.extend(self.prices.find(query))
        if symbol and token not in (None, ""):
            token_query = {"token": str(token).strip()}
            if provider:
                token_query["provider"] = str(provider).strip().lower()
            candidates.extend(self._cache_candidates(token_query))
            candidates.extend(self.prices.find(token_query))

        seen = set()
        unique = []
        for row in candidates:
            row_id = str(row.get("_id") or (row.get("provider"), row.get("symbol"), row.get("token")))
            if row_id in seen:
                continue
            seen.add(row_id)
            unique.append(row)
        unique.sort(key=lambda row: _as_aware(row.get("updated_at")), reverse=True)
        now = utcnow()
        for row in unique:
            stale_after = _as_aware(row.get("stale_after")) if row.get("stale_after") else None
            if require_fresh and stale_after and stale_after < now:
                continue
            return dict(row)
        return None

    def has_fresh_prices(self, symbols, provider=None):
        missing = []
        stale = []
        providers = {}
        for symbol in sorted({str(item or "").strip().upper() for item in symbols or [] if str(item or "").strip()}):
            row = self.latest_price(symbol=symbol, provider=provider, require_fresh=False)
            if not row:
                missing.append(symbol)
                continue
            stale_after = _as_aware(row.get("stale_after")) if row.get("stale_after") else None
            if stale_after and stale_after < utcnow():
                stale.append(symbol)
                continue
            providers[symbol] = str(row.get("provider") or "").strip().lower()
        return {
            "ready": not missing and not stale,
            "missing": missing,
            "stale": stale,
            "providers": providers,
        }

    def update_health(self, provider, **fields):
        now = utcnow()
        provider = str(provider or "").strip().lower()
        payload = {"provider": provider, "updated_at": now, **fields}
        self.health.update_one(
            {"provider": provider},
            {"$set": payload, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return self.health.find_one({"provider": provider}) or payload

    def get_health(self, provider):
        provider = str(provider or "").strip().lower()
        return self.health.find_one({"provider": provider}) or {
            "provider": provider,
            "status": "not_started",
            "connected": False,
        }

    def update_global_health(self, **fields):
        return self.update_health(self.GLOBAL_HEALTH_PROVIDER, **fields)

    def get_global_health(self):
        row = self.get_health(self.GLOBAL_HEALTH_PROVIDER)
        row.setdefault("active_provider", "")
        row.setdefault("provider_chain", [])
        row.setdefault("failed_providers", [])
        return row

    def active_provider(self):
        return str(self.get_global_health().get("active_provider") or "").strip().lower()
