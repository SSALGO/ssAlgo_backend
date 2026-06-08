import datetime
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List


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
        "block_on_broker_disconnect": True,
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
        return settings

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

    def check_order(self, order, mode: str = "live") -> RiskCheckResult:
        user = getattr(order, "user", "")
        broker = getattr(order, "broker", "")
        symbol = getattr(order, "symbol", "")
        settings = self.settings_for_user(user)
        checks = []

        if settings.get("kill_switch"):
            return RiskCheckResult(False, "Trading kill switch is enabled", checks)

        if mode != "paper" and (settings.get("paper_only") or not settings.get("live_enabled")):
            return RiskCheckResult(False, "Live trading is disabled; use paper mode", checks)
        checks.append("mode")

        max_orders = int(settings.get("max_orders_per_day") or 0)
        if max_orders and self._orders_today(user) >= max_orders:
            return RiskCheckResult(False, "Daily order limit reached", checks)
        checks.append("orders_per_day")

        max_positions = int(settings.get("max_open_positions") or 0)
        if max_positions and self._open_positions(user) >= max_positions:
            return RiskCheckResult(False, "Open position limit reached", checks)
        checks.append("open_positions")

        if mode != "paper" and settings.get("block_on_broker_disconnect"):
            health = self._broker_health(user, broker)
            websocket_status = str(health.get("websocket_status") or "").lower()
            login_status = str(health.get("login_status") or "").lower()
            if login_status and login_status != "connected":
                return RiskCheckResult(False, "Broker login is not connected", checks)
            if websocket_status and websocket_status not in {"connected", "unknown"}:
                return RiskCheckResult(False, "Broker websocket is disconnected", checks)
        checks.append("broker_health")

        if not symbol:
            return RiskCheckResult(False, "Order symbol is required", checks)
        checks.append("symbol")

        return RiskCheckResult(True, checks=checks)
