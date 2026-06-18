import datetime

from app.domain.market_data import MarketPriceRepository
from app.workers.control import WorkerControlService


class LiveReadinessService:
    DEFAULT_MIN_PAPER_ORDERS = 10
    DEFAULT_MIN_PAPER_DAYS = 2

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _now():
        return datetime.datetime.now(datetime.UTC)

    def check_user(self, user: str, min_orders=None, min_days=None):
        min_orders = int(min_orders or self.DEFAULT_MIN_PAPER_ORDERS)
        min_days = int(min_days or self.DEFAULT_MIN_PAPER_DAYS)
        since = self._now() - datetime.timedelta(days=min_days)
        paper_orders = list(self.db["normalized_orders"].find({
            "user": user,
            "broker": "paper",
            "status": {"$in": ["filled", "submitted"]},
            "created_at": {"$gte": since},
        }))
        broker_row = self.db["broker"].find_one({"user": user}) or {}
        selected_broker = broker_row.get("selectedbroker") or "paper"
        broker_health = self.db["broker_health"].find_one({"user": user, "broker": selected_broker}) or {}
        risk_settings = self.db["risk_settings"].find_one({"user": user}) or {}
        worker_status = WorkerControlService(self.db).get_status()
        feed_health = MarketPriceRepository(self.db).get_global_health()
        checks = {
            "paper_burn_in": len(paper_orders) >= min_orders,
            "broker_selected": selected_broker != "paper",
            "broker_login_connected": broker_health.get("login_status") == "connected",
            "worker_running": worker_status.get("healthy") is True,
            "market_feed_connected": (
                feed_health.get("connected") is True
                or feed_health.get("status") == "connected"
            ),
            "risk_settings_present": bool(risk_settings),
            "kill_switch_off": not bool(risk_settings.get("kill_switch")),
        }
        missing = [name for name, ok in checks.items() if not ok]
        return {
            "user": user,
            "ready": not missing,
            "selected_broker": selected_broker,
            "paper_order_count": len(paper_orders),
            "min_paper_orders": min_orders,
            "min_paper_days": min_days,
            "worker_status": worker_status,
            "market_feed_health": feed_health,
            "checks": checks,
            "missing": missing,
        }
