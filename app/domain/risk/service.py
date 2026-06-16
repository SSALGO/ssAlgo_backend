import datetime
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.core.trading_debug import trading_event


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str = ""
    checks: List[str] = field(default_factory=list)

    def to_dict(self):
        return {"allowed": self.allowed, "reason": self.reason, "checks": self.checks}


class RiskControlService:
    DEFAULTS = {
        "live_enabled": False,
        "paper_only": True,
        "kill_switch": False,
        "max_daily_loss": 0,
        "max_orders_per_day": 50,
        "max_open_positions": 10,
        "max_symbol_exposure": 0,
        "max_order_quantity": 0,
        "block_on_broker_disconnect": True,
        "require_market_hours": False,
        "market_open": "09:15",
        "market_close": "15:30",
        "require_fresh_quote": False,
        "max_quote_age_seconds": 300,
        "duplicate_signal_window_seconds": 0,
    }
    PRODUCTION_LIVE_DEFAULTS = {
        "live_enabled": True,
        "paper_only": False,
        "kill_switch": False,
        "block_on_broker_disconnect": True,
        "require_market_hours": True,
        "market_open": "09:15",
        "market_close": "15:30",
        "require_fresh_quote": True,
        "max_quote_age_seconds": 60,
        "duplicate_signal_window_seconds": 30,
        "max_orders_per_day": 25,
        "max_open_positions": 5,
    }

    def __init__(self, db=None):
        self.db = db

    @staticmethod
    def _today_start():
        now = datetime.datetime.now(datetime.UTC)
        return datetime.datetime(now.year, now.month, now.day, tzinfo=datetime.UTC)

    @staticmethod
    def _env_live_enabled():
        return os.getenv("SSLAGO_LIVE_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

    def settings_for_user(self, user: str) -> Dict[str, Any]:
        settings = dict(self.DEFAULTS)
        if self._env_live_enabled():
            settings["live_enabled"] = True
            settings["paper_only"] = False
        if self.db is not None:
            row = self.db["risk_settings"].find_one({"user": user}) or {}
            settings.update({key: value for key, value in row.items() if key != "_id"})
            profile = self.db["users"].find_one({"username": user}) or {}
            if profile.get("trade_limit") and not row.get("max_orders_per_day"):
                settings["max_orders_per_day"] = profile.get("trade_limit")
            if profile.get("day_loss_limit") and not row.get("max_daily_loss"):
                settings["max_daily_loss"] = profile.get("day_loss_limit")
        return settings

    def apply_production_live_defaults(self, user: str, overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if self.db is None:
            return {**self.PRODUCTION_LIVE_DEFAULTS, **(overrides or {})}
        settings = {**self.PRODUCTION_LIVE_DEFAULTS, **(overrides or {})}
        row = {
            "user": user,
            **settings,
            "updated_at": datetime.datetime.now(datetime.UTC),
            "profile": "production_live_defaults",
        }
        self.db["risk_settings"].update_one(
            {"user": user},
            {"$set": row, "$setOnInsert": {"created_at": datetime.datetime.now(datetime.UTC)}},
            upsert=True,
        )
        return self.settings_for_user(user)

    def _orders_today(self, user: str) -> int:
        if self.db is None:
            return 0
        return self.db["normalized_orders"].count_documents({
            "user": user,
            "created_at": {"$gte": self._today_start()},
        })

    def _open_positions(self, user: str) -> int:
        if self.db is None:
            return 0
        return self.db["paper_positions"].count_documents({
            "user": user,
            "net_quantity": {"$ne": 0},
        })

    def _broker_health(self, user: str, broker: str) -> Dict[str, Any]:
        if self.db is None:
            return {}
        return self.db["broker_health"].find_one({"user": user, "broker": broker}) or {}

    @staticmethod
    def _ist_now():
        return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

    @staticmethod
    def _parse_hhmm(value: str):
        hour, minute = [int(part) for part in str(value or "").split(":", 1)]
        return datetime.time(hour, minute)

    def _inside_market_hours(self, settings: Dict[str, Any]) -> bool:
        now_time = self._ist_now().time()
        try:
            market_open = self._parse_hhmm(settings.get("market_open", "09:15"))
            market_close = self._parse_hhmm(settings.get("market_close", "15:30"))
        except Exception:
            return False
        return market_open <= now_time <= market_close

    @staticmethod
    def _coerce_datetime(value):
        if value is None or value == "":
            return None
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime.UTC)
                return parsed
            except ValueError:
                return None
        return None

    def _quote_time(self, user: str, symbol: str, metadata: Dict[str, Any]):
        for key in ("quote_time", "last_quote_time", "ltp_time", "timestamp"):
            parsed = self._coerce_datetime(metadata.get(key))
            if parsed:
                return parsed
        if self.db is None:
            return None
        row = (
            self.db["paper_quotes"].find_one({"user": user, "symbol": symbol})
            or self.db["market_quotes"].find_one({"user": user, "symbol": symbol})
            or self.db["market_prices"].find_one({"symbol": str(symbol or "").strip().upper()})
            or self.db["paper_quotes"].find_one({"symbol": symbol})
            or self.db["market_quotes"].find_one({"symbol": symbol})
        )
        if not row:
            return None
        return self._coerce_datetime(row.get("updated_at") or row.get("last_quote_time") or row.get("timestamp"))

    def _position_close_timestamp(self, row: dict) -> int | None:
        for key in ("exittime", "time", "closed_at", "updated_at"):
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, datetime.datetime):
                return int(value.timestamp())
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _realized_pnl_today(self, user: str) -> float:
        if self.db is None:
            return 0.0
        total = 0.0
        start_ts = int(self._today_start().timestamp())
        for row in self.db["Opositions"].find({"user": user, "status": "close"}):
            close_ts = self._position_close_timestamp(row)
            if close_ts is None or close_ts < start_ts:
                continue
            try:
                total += float(row.get("pnl") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _duplicate_idempotency_key(self, user: str, key: str, current_job_id=None) -> bool:
        if self.db is None or not key:
            return False
        for row in self.db["strategy_jobs"].find({"user": user, "idempotency_key": key, "status": {"$ne": "failed"}}):
            if current_job_id is not None and row.get("_id") == current_job_id:
                continue
            return True
        return False

    def _duplicate_signal(self, user: str, symbol: str, side: str, strategy_id: str, window_seconds: int) -> bool:
        if self.db is None or not strategy_id or window_seconds <= 0:
            return False
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=window_seconds)
        return self.db["normalized_orders"].count_documents({
            "user": user,
            "symbol": symbol,
            "side": side,
            "strategy_id": strategy_id,
            "created_at": {"$gte": since},
        }, limit=1) > 0

    def _strategy_settings(self, user: str, strategy_id: str) -> Dict[str, Any]:
        if self.db is None or not strategy_id:
            return {}
        return self.db["strategy_risk_settings"].find_one({"user": user, "strategy_id": strategy_id}) or {}

    def _strategy_trades_today(self, user: str, strategy_id: str) -> int:
        if self.db is None or not strategy_id:
            return 0
        return self.db["normalized_orders"].count_documents({
            "user": user,
            "strategy_id": strategy_id,
            "created_at": {"$gte": self._today_start()},
        })

    def _strategy_realized_pnl_today(self, user: str, strategy_id: str) -> float:
        if self.db is None or not strategy_id:
            return 0.0
        total = 0.0
        start_ts = int(self._today_start().timestamp())
        for row in self.db["Opositions"].find({"user": user, "botcode": strategy_id, "status": "close"}):
            close_ts = self._position_close_timestamp(row)
            if close_ts is None or close_ts < start_ts:
                continue
            try:
                total += float(row.get("pnl") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _last_strategy_loss_time(self, user: str, strategy_id: str):
        if self.db is None or not strategy_id:
            return None
        latest = None
        for row in self.db["Opositions"].find({"user": user, "botcode": strategy_id, "status": "close"}):
            try:
                pnl = float(row.get("pnl") or 0)
            except (TypeError, ValueError):
                continue
            if pnl >= 0:
                continue
            closed_at = self._coerce_datetime(row.get("closed_at") or row.get("updated_at") or row.get("exittime") or row.get("time"))
            if closed_at and (latest is None or closed_at > latest):
                latest = closed_at
        return latest

    def check_order(self, order, mode: str = "live") -> RiskCheckResult:
        user = getattr(order, "user", "")
        broker = getattr(order, "broker", "")
        symbol = getattr(order, "symbol", "")
        side = str(getattr(order, "side", "") or "").upper()
        quantity = int(getattr(order, "quantity", 0) or 0)
        metadata = getattr(order, "metadata", {}) or {}
        strategy_id = str(getattr(order, "strategy_id", "") or metadata.get("strategy_id") or metadata.get("botcode") or "").strip()
        settings = self.settings_for_user(user)
        checks = []
        trading_event(
            "risk_validation_started",
            user=user,
            broker=broker,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            mode=mode,
            settings=settings,
            orders_today=self._orders_today(user),
            open_positions=self._open_positions(user),
            realized_pnl_today=self._realized_pnl_today(user),
        )

        if settings.get("kill_switch"):
            return RiskCheckResult(False, "Trading kill switch is enabled", checks)

        if mode != "paper" and (settings.get("paper_only") or not settings.get("live_enabled")):
            return RiskCheckResult(False, "Live trading is disabled; use paper mode", checks)
        checks.append("mode")

        if mode != "paper" and settings.get("require_market_hours") and not self._inside_market_hours(settings):
            return RiskCheckResult(False, "Order is outside configured market hours", checks)
        checks.append("market_hours")

        if side not in {"BUY", "SELL"}:
            return RiskCheckResult(False, "Order side must be BUY or SELL", checks)
        checks.append("side")

        if quantity <= 0:
            return RiskCheckResult(False, "Order quantity must be greater than zero", checks)
        checks.append("quantity")

        max_order_quantity = int(settings.get("max_order_quantity") or 0)
        if max_order_quantity and quantity > max_order_quantity:
            return RiskCheckResult(False, "Order quantity exceeds max order quantity", checks)
        checks.append("max_order_quantity")

        if mode != "paper":
            idempotency_key = str(metadata.get("idempotency_key") or "").strip()
            if not idempotency_key:
                return RiskCheckResult(False, "Live orders require an idempotency key", checks)
            if self._duplicate_idempotency_key(user, idempotency_key, metadata.get("job_id")):
                return RiskCheckResult(False, "Duplicate live order idempotency key", checks)
        checks.append("idempotency")

        duplicate_window = int(settings.get("duplicate_signal_window_seconds") or 0)
        if self._duplicate_signal(user, symbol, side, strategy_id, duplicate_window):
            return RiskCheckResult(False, "Duplicate strategy signal suppressed", checks)
        checks.append("duplicate_signal")

        max_orders = int(settings.get("max_orders_per_day") or 0)
        if max_orders and self._orders_today(user) >= max_orders:
            return RiskCheckResult(False, "Daily order limit reached", checks)
        checks.append("orders_per_day")

        max_daily_loss = float(settings.get("max_daily_loss") or 0)
        if max_daily_loss and self._realized_pnl_today(user) <= -abs(max_daily_loss):
            return RiskCheckResult(False, "Daily loss limit reached", checks)
        checks.append("daily_loss")

        strategy_settings = self._strategy_settings(user, strategy_id)
        max_strategy_trades = int(strategy_settings.get("max_trades_per_day") or 0)
        if max_strategy_trades and self._strategy_trades_today(user, strategy_id) >= max_strategy_trades:
            return RiskCheckResult(False, "Strategy daily trade limit reached", checks)
        checks.append("strategy_trades_per_day")

        max_strategy_loss = float(strategy_settings.get("max_daily_loss") or 0)
        if max_strategy_loss and self._strategy_realized_pnl_today(user, strategy_id) <= -abs(max_strategy_loss):
            return RiskCheckResult(False, "Strategy daily loss limit reached", checks)
        checks.append("strategy_daily_loss")

        cooldown_seconds = int(strategy_settings.get("cooldown_after_loss_seconds") or 0)
        last_loss_time = self._last_strategy_loss_time(user, strategy_id)
        if cooldown_seconds and last_loss_time:
            elapsed = datetime.datetime.now(datetime.UTC) - last_loss_time.astimezone(datetime.UTC)
            if elapsed.total_seconds() < cooldown_seconds:
                return RiskCheckResult(False, "Strategy is cooling down after a loss", checks)
        checks.append("strategy_cooldown")

        max_positions = int(settings.get("max_open_positions") or 0)
        if max_positions and self._open_positions(user) >= max_positions:
            return RiskCheckResult(False, "Open position limit reached", checks)
        checks.append("open_positions")

        if mode != "paper" and settings.get("block_on_broker_disconnect"):
            health = self._broker_health(user, broker)
            login_status = str(health.get("login_status") or "").lower()
            if login_status != "connected":
                return RiskCheckResult(False, "Broker login is not connected", checks)
        checks.append("broker_health")

        if not symbol:
            return RiskCheckResult(False, "Order symbol is required", checks)
        checks.append("symbol")

        if mode != "paper" and settings.get("require_fresh_quote"):
            quote_time = self._quote_time(user, symbol, metadata)
            max_age = int(settings.get("max_quote_age_seconds") or 300)
            if not quote_time:
                return RiskCheckResult(False, "Fresh quote is required", checks)
            quote_age = (datetime.datetime.now(datetime.UTC) - quote_time.astimezone(datetime.UTC)).total_seconds()
            if quote_age > max_age:
                return RiskCheckResult(False, "Quote is stale", checks)
        checks.append("fresh_quote")

        return RiskCheckResult(True, checks=checks)
