from app.core.config import AppConfig
from app.core.trading_debug import trading_event, trading_exception
from app.domain.brokers.registry import normalize_broker_id
from app.domain.market_data.price_repository import MarketPriceRepository, utcnow
from app.domain.market_data.providers import PROVIDER_CLASSES, ZerodhaFeedProvider


class MarketFeedManager:
    """Owns the single active shared market-data provider and fallback chain."""

    def __init__(self, db, provider=None, price_repository=None, provider_classes=None):
        self.db = db
        self.prices = price_repository or MarketPriceRepository(db)
        self.provider_classes = provider_classes or PROVIDER_CLASSES
        self.provider_chain = self._provider_chain(provider=provider)
        self.provider = self.provider_chain[0]
        self.failover_mode = AppConfig.MARKET_FEED_FAILOVER_MODE or "connect_failure_only"
        self._providers = {}
        self.active_provider = ""

    def _provider_chain(self, provider=None):
        configured = [provider] if provider else list(AppConfig.MARKET_FEED_PROVIDERS or [])
        if not configured:
            configured = [AppConfig.MARKET_FEED_PROVIDER or "zerodha"]
        chain = []
        for item in configured:
            normalized = normalize_broker_id(item)
            if normalized and normalized not in chain:
                chain.append(normalized)
        return chain or ["zerodha"]

    def _provider_instance(self, provider):
        provider = normalize_broker_id(provider)
        if provider not in self._providers:
            provider_class = self.provider_classes.get(provider)
            if provider_class is None:
                raise RuntimeError(f"Market feed provider {provider} is not supported")
            self._providers[provider] = provider_class(self.db, self.prices)
        return self._providers[provider]

    def active_symbols(self, user=None):
        if self.db is None:
            return []
        query = {
            "live": True,
            "$or": [
                {"status": "opened"},
                {"position": "in"},
            ],
        }
        if user:
            query["user"] = user
        symbols = set()
        for strategy in self.db["strategies"].find(query):
            raw_symbols = strategy.get("symbol") or strategy.get("symbol[]") or []
            if isinstance(raw_symbols, str):
                raw_symbols = [raw_symbols]
            for symbol in raw_symbols:
                symbol_text = str(symbol or "").strip().upper()
                if symbol_text:
                    symbols.add(symbol_text)
        return sorted(symbols)

    @staticmethod
    def normalize_symbols(symbols):
        return sorted({
            str(symbol or "").strip().upper()
            for symbol in symbols or []
            if str(symbol or "").strip()
        })

    def _disconnect_inactive_providers(self, active_provider):
        for provider, instance in list(self._providers.items()):
            if provider == active_provider:
                continue
            try:
                instance.disconnect()
            except Exception as exc:
                trading_exception("market_feed_disconnect_error", exc, provider=provider)

    def _snapshot_warmup_result(self, provider, instance, symbols, failed):
        warm_symbols = getattr(instance, "warm_symbols", None)
        if not callable(warm_symbols):
            return None
        try:
            warm_result = warm_symbols(symbols)
        except Exception as exc:
            trading_exception("market_feed_snapshot_warmup_failed", exc, provider=provider)
            return None
        price_status = self.prices.has_fresh_prices(symbols, provider=None)
        if not price_status.get("ready"):
            return None
        result = {
            "success": True,
            "provider": provider,
            "active_provider": provider,
            "provider_chain": list(self.provider_chain),
            "status": "connected",
            "message": f"Market prices warmed via {provider} snapshot",
            "symbols": symbols,
            "instrument_tokens": warm_result.get("instrument_tokens", []),
            "missing_symbols": warm_result.get("missing_symbols", []),
            "connect_result": {"connected": True, "provider": provider, "mode": "snapshot"},
        }
        self.active_provider = provider
        self.prices.update_health(
            provider,
            connected=True,
            status="connected",
            subscribed_symbols=symbols,
            instrument_tokens=result["instrument_tokens"],
            missing_symbols=result["missing_symbols"],
            last_error="",
            connected_at=utcnow(),
            mode="snapshot",
        )
        self.prices.update_global_health(
            connected=True,
            status="connected",
            active_provider=provider,
            provider_chain=list(self.provider_chain),
            failed_providers=failed,
            last_error="",
            connected_at=utcnow(),
            mode="snapshot",
        )
        trading_event("market_feed_snapshot_warmed", provider=provider, result=result, force=True)
        return result

    def refresh_subscriptions(self, user=None, broker=None, symbols=None):
        symbols = self.normalize_symbols(symbols) if symbols is not None else self.active_symbols(user=user)
        if broker:
            trading_event(
                "market_feed_subscription_request",
                provider_chain=self.provider_chain,
                requested_broker=broker,
                user=user,
            )

        failed = []
        errors = {}
        for provider in self.provider_chain:
            try:
                instance = self._provider_instance(provider)
                connect_result = instance.connect()
                subscribe_result = instance.subscribe(symbols)
                self.active_provider = provider
                self._disconnect_inactive_providers(provider)
                result = {
                    "success": True,
                    "provider": provider,
                    "active_provider": provider,
                    "provider_chain": list(self.provider_chain),
                    "status": "connected",
                    "message": f"Shared market feed connected via {provider}",
                    "symbols": symbols,
                    "instrument_tokens": subscribe_result.get("instrument_tokens", []),
                    "missing_symbols": subscribe_result.get("missing_symbols", []),
                    "connect_result": connect_result,
                }
                self.prices.update_health(
                    provider,
                    connected=True,
                    status="connected",
                    subscribed_symbols=symbols,
                    instrument_tokens=result["instrument_tokens"],
                    missing_symbols=result["missing_symbols"],
                    last_error="",
                    connected_at=utcnow(),
                )
                self.prices.update_global_health(
                    connected=True,
                    status="connected",
                    active_provider=provider,
                    provider_chain=list(self.provider_chain),
                    failed_providers=failed,
                    last_error="",
                    connected_at=utcnow(),
                )
                trading_event("market_feed_started", provider=provider, result=result, force=True)
                trading_event("market_feed_subscribed", provider=provider, symbols=symbols, instrument_tokens=result["instrument_tokens"])
                return result
            except Exception as exc:
                message = str(exc)
                failed.append(provider)
                errors[provider] = message
                self.prices.update_health(
                    provider,
                    connected=False,
                    status="failed",
                    subscribed_symbols=symbols,
                    last_error=message,
                )
                trading_exception("market_feed_provider_failed", exc, provider=provider, user=user)
                snapshot_result = self._snapshot_warmup_result(provider, instance, symbols, failed)
                if snapshot_result:
                    return snapshot_result

        result = {
            "success": False,
            "provider": "",
            "active_provider": "",
            "provider_chain": list(self.provider_chain),
            "status": "disconnected",
            "message": "No market feed provider connected",
            "symbols": symbols,
            "failed_providers": failed,
            "errors": errors,
        }
        self.active_provider = ""
        self.prices.update_global_health(
            connected=False,
            status="disconnected",
            active_provider="",
            provider_chain=list(self.provider_chain),
            failed_providers=failed,
            last_error=result["message"],
        )
        trading_event("market_feed_disconnected", result=result, force=True)
        return result

    def ensure_symbols(self, symbols, user=None, broker=None):
        """Warm the central feed for symbols that are not opened yet."""
        return self.refresh_subscriptions(
            user=user,
            broker=broker,
            symbols=self.normalize_symbols(symbols),
        )

    def on_kite_tick(self, tick):
        provider = self._providers.get("zerodha")
        if provider is None:
            provider = ZerodhaFeedProvider(self.db, self.prices)
            self._providers["zerodha"] = provider
        return provider.on_tick(tick)
