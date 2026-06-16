import logging
import os
import threading
import time

from app.core.trading_debug import trading_event, trading_exception
from app.domain.brokers.adapters import BrokerAdapterFactory, BrokerCredentials, BrokerOrder
from app.domain.brokers.kite import KiteService
from app.domain.brokers.health import BrokerHealthService
from app.domain.brokers.registry import normalize_broker_id
from app.domain.audit.service import AuditLogService
from app.domain.market_data import kite_market_data
from app.domain.orders.lifecycle import OrderLifecycleService
from app.domain.risk.service import RiskControlService
from app.workers.control import WorkerControlService
from app.api.fastapi_schemas import WorkerOrderRequest


logger = logging.getLogger(__name__)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class TradingWorker:
    """Owns broker maintenance, market subscriptions, and queued trade execution."""

    def __init__(self, health_service=None, db=None, interval_seconds=1, relogin_interval_seconds=60, subscription_interval_seconds=30):
        self.db = db
        self.audit = AuditLogService(db) if db is not None else None
        self.health_service = health_service or (BrokerHealthService(db) if db is not None else None)
        self.order_lifecycle = OrderLifecycleService(db, audit_service=self.audit) if db is not None else None
        self.risk_service = RiskControlService(db)
        self.adapter_factory = BrokerAdapterFactory(
            db=db,
            health_service=self.health_service,
            order_lifecycle=self.order_lifecycle,
            risk_service=self.risk_service,
        ) if db is not None else None
        self.control = WorkerControlService(db) if db is not None else None
        self.interval_seconds = interval_seconds
        self.relogin_interval_seconds = relogin_interval_seconds
        self.subscription_interval_seconds = subscription_interval_seconds
        self.enable_broker_maintenance = _env_bool(
            "SSLAGO_ENABLE_BROKER_MAINTENANCE",
            False,
        )
        self._stop_event = threading.Event()
        self._thread = None
        self._last_relogin = 0
        self._last_subscription = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _users_with_broker_credentials(self, user=None, broker=None):
        if self.db is None:
            return []
        rows = []
        seen = set()
        execution_users = None
        if not user:
            execution_users = {
                strategy.get("user")
                for strategy in self.db["strategies"].find({
                    "live": True,
                    "$or": [
                        {"status": "opened"},
                        {"position": "in"},
                    ]
                })
                if strategy.get("user")
            }
        query = {"$or": [
            {"selectedbroker": {"$exists": True, "$ne": ""}},
            {"selected_broker": {"$exists": True, "$ne": ""}},
        ]}
        if user:
            query["user"] = user
        for selected in self.db["broker"].find(query):
            api_user = selected.get("user")
            if execution_users is not None and api_user not in execution_users:
                continue
            api_broker = normalize_broker_id(
                selected.get("selectedbroker") or selected.get("selected_broker")
            )
            if broker and api_broker != normalize_broker_id(broker):
                continue
            pair = (api_user, api_broker)
            if api_user and api_broker and pair not in seen:
                seen.add(pair)
                rows.append(pair)
        return rows

    def refresh_broker_logins(self, user=None, broker=None):
        refreshed = []
        for user, broker in self._users_with_broker_credentials(user=user, broker=broker):
            try:
                adapter = self.adapter_factory.create(broker)
                result = adapter.login(BrokerCredentials(user=user, broker=broker))
                trading_event("broker_login_result", user=user, broker=broker, result=result)
                refreshed.append({"user": user, "broker": broker, "result": result})
            except Exception as exc:
                trading_exception("broker_login_error", exc, user=user, broker=broker)
                if self.health_service:
                    self.health_service.update_health(
                        user,
                        broker,
                        login_status="rejected",
                        last_error=str(exc),
                    )
                refreshed.append({"user": user, "broker": broker, "error": str(exc)})
        return refreshed

    def _active_strategy_symbols(self):
        if self.db is None:
            return {}
        symbols_by_user_broker = {}
        for strategy in self.db["strategies"].find({
            "$or": [
                {"status": "opened"},
                {"position": "in"},
            ]
        }):
            user = strategy.get("user")
            if not user:
                continue
            broker_row = self.db["broker"].find_one({"user": user}) or {}
            broker = broker_row.get("selectedbroker") or "paper"
            symbols = strategy.get("symbol") or strategy.get("symbol[]") or []
            if isinstance(symbols, str):
                symbols = [symbols]
            key = (user, broker)
            symbols_by_user_broker.setdefault(key, set()).update(str(symbol).strip() for symbol in symbols if str(symbol).strip())
        return symbols_by_user_broker

    def _kite_instrument_tokens(self, symbols):
        tokens = []
        missing = []
        if self.db is None:
            return tokens, list(symbols or [])
        for symbol in symbols or []:
            symbol_text = str(symbol or "").strip().upper()
            if not symbol_text:
                continue
            row = (
                self.db["kite_instruments"].find_one({"tradingsymbol": symbol_text})
                or self.db["kite_instruments"].find_one({"tradingsymbol": symbol_text, "exchange": "NFO"})
                or self.db["kite_instruments"].find_one({"tradingsymbol": symbol_text, "exchange": "NSE"})
            )
            if row and row.get("instrument_token"):
                tokens.append(int(row["instrument_token"]))
            else:
                missing.append(symbol_text)
        return sorted(set(tokens)), missing

    def _refresh_zerodha_subscription(self, user, symbols):
        service = KiteService(self.db)
        access_token = service.access_token(user)
        tokens, missing_symbols = self._kite_instrument_tokens(symbols)
        connect_result = kite_market_data.connect(
            service.api_key,
            access_token,
            threaded=True,
        )
        subscribed = kite_market_data.subscribe_instruments(tokens) if tokens else []
        result = {
            "success": True,
            "broker": "zerodha",
            "status": "connected",
            "message": "Kite websocket connected",
            "symbols": sorted(symbols or []),
            "instrument_tokens": subscribed,
            "missing_symbols": missing_symbols,
            "connect_result": connect_result,
        }
        if missing_symbols:
            result["message"] = "Kite websocket connected; some symbols are missing instrument tokens"
        trading_event(
            "kite_worker_websocket_subscription",
            user=user,
            broker="zerodha",
            symbols=sorted(symbols or []),
            instrument_tokens=subscribed,
            missing_symbols=missing_symbols,
            result=result,
            force=True,
        )
        return result

    def refresh_subscriptions(self, user=None, broker=None):
        results = []
        target_user = user
        target_broker = normalize_broker_id(broker) if broker else broker
        for (strategy_user, strategy_broker), symbols in self._active_strategy_symbols().items():
            if target_user and target_user != strategy_user:
                continue
            strategy_broker = normalize_broker_id(strategy_broker)
            if target_broker and target_broker != strategy_broker:
                continue
            try:
                normalized_broker = normalize_broker_id(strategy_broker)
                if normalized_broker == "zerodha":
                    result = self._refresh_zerodha_subscription(strategy_user, symbols)
                else:
                    adapter = self.adapter_factory.create(strategy_broker)
                    adapter.login(BrokerCredentials(user=strategy_user, broker=strategy_broker))
                    result = adapter.subscribe(sorted(symbols), user=strategy_user)
                trading_event(
                    "market_subscription_result",
                    user=strategy_user,
                    broker=normalized_broker,
                    symbols=sorted(symbols),
                    result=result,
                )
                if self.health_service:
                    self.health_service.update_health(
                        strategy_user,
                        normalized_broker,
                        websocket_status="connected" if result.get("success") else result.get("status", "unsupported"),
                        last_error="" if result.get("success") else result.get("message", ""),
                    )
                results.append({"user": strategy_user, "broker": normalized_broker, "symbols": sorted(symbols), "result": result})
            except Exception as exc:
                trading_exception(
                    "market_subscription_error",
                    exc,
                    user=strategy_user,
                    broker=strategy_broker,
                    symbols=sorted(symbols),
                )
                if self.health_service:
                    self.health_service.update_health(
                        strategy_user,
                        strategy_broker,
                        websocket_status="disconnected",
                        last_error=str(exc),
                    )
                results.append({"user": strategy_user, "broker": strategy_broker, "symbols": sorted(symbols), "error": str(exc)})
        return results

    def _strategy_job_collection(self):
        return self.db["strategy_jobs"] if self.db is not None else None

    def enqueue_strategy_order(self, payload):
        jobs = self._strategy_job_collection()
        if jobs is None:
            raise RuntimeError("Worker database is not configured")
        row = WorkerOrderRequest(**(payload or {})).model_dump()
        row.setdefault("status", "pending")
        row.setdefault("mode", "paper")
        row.setdefault("created_at", WorkerControlService.now())
        row.setdefault("updated_at", WorkerControlService.now())
        result = jobs.insert_one(row)
        row["_id"] = str(result.inserted_id)
        trading_event("order_request_generated", job=row)
        if self.audit:
            self.audit.record(
                "strategy_job_enqueued",
                user=row.get("user", ""),
                resource_type="strategy_job",
                resource_id=row["_id"],
                details={"mode": row.get("mode"), "symbol": row.get("symbol"), "side": row.get("side"), "strategy_id": row.get("strategy_id")},
            )
        return row

    def _next_strategy_job(self):
        jobs = self._strategy_job_collection()
        if jobs is None:
            return None
        if hasattr(jobs, "find_one_and_update"):
            return jobs.find_one_and_update(
                {"status": "pending"},
                {"$set": {"status": "processing", "updated_at": WorkerControlService.now()}},
            )
        row = jobs.find_one({"status": "pending"})
        if row:
            jobs.update_one({"_id": row["_id"]}, {"$set": {"status": "processing", "updated_at": WorkerControlService.now()}})
        return row

    def _complete_strategy_job(self, job_id, result=None, error=""):
        jobs = self._strategy_job_collection()
        if jobs is None:
            return
        jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": "failed" if error else "completed",
                    "result": result or {},
                    "error": error,
                    "updated_at": WorkerControlService.now(),
                }
            },
        )

    def process_strategy_jobs(self, limit=25):
        processed = []
        for _ in range(limit):
            job = self._next_strategy_job()
            if not job:
                break
            try:
                mode = str(job.get("mode") or "paper").lower()
                broker = "paper" if mode == "paper" else str(job.get("broker") or "")
                if not broker:
                    broker_row = self.db["broker"].find_one({"user": job.get("user")}) or {}
                    broker = broker_row.get("selectedbroker") or "paper"
                order = BrokerOrder(
                    user=job["user"],
                    broker=broker,
                    symbol=str(job.get("symbol", "")).strip().upper(),
                    side=str(job.get("side", "BUY")).strip().upper(),
                    quantity=int(job.get("quantity", 1)),
                    exchange=str(job.get("exchange", "")),
                    product_type=str(job.get("product_type", "INTRADAY")),
                    order_type=str(job.get("order_type", "MARKET")),
                    price=float(job.get("price", 1) or 1),
                    strategy_id=str(job.get("strategy_id") or job.get("botcode") or ""),
                    metadata={**dict(job.get("metadata") or {}), **dict(job), "job_id": job.get("_id")},
                )
                trading_event(
                    "order_payload_ready",
                    job_id=job.get("_id"),
                    user=order.user,
                    broker=broker,
                    strategy_id=order.strategy_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    mode=mode,
                    metadata=order.metadata,
                )
                adapter = self.adapter_factory.create(broker)
                adapter.login(BrokerCredentials(user=order.user, broker=broker))
                result = adapter.place_order(order)
                self._complete_strategy_job(job["_id"], result=result)
                if self.audit:
                    self.audit.record(
                        "strategy_job_completed",
                        user=order.user,
                        resource_type="strategy_job",
                        resource_id=job["_id"],
                        details={"broker": broker, "result": result},
                    )
                processed.append({"job_id": str(job["_id"]), "result": result})
            except Exception as exc:
                trading_exception(
                    "strategy_job_failed",
                    exc,
                    job_id=job.get("_id"),
                    user=job.get("user"),
                    strategy_id=job.get("strategy_id") or job.get("botcode"),
                )
                self._complete_strategy_job(job["_id"], error=str(exc))
                if self.audit:
                    self.audit.record(
                        "strategy_job_failed",
                        user=str(job.get("user") or ""),
                        resource_type="strategy_job",
                        resource_id=job.get("_id"),
                        status="failed",
                        details={"error": str(exc)},
                    )
                processed.append({"job_id": str(job["_id"]), "error": str(exc)})
        return processed

    def handle_command(self, command):
        name = command.get("command")
        payload = command.get("payload") or {}
        if name == "stop":
            self._stop_event.set()
            return {"stopping": True}
        if name in {"relogin", "refresh_brokers"}:
            return {"brokers": self.refresh_broker_logins(user=payload.get("user"), broker=payload.get("broker"))}
        if name in {"subscribe", "refresh_subscriptions"}:
            return {"subscriptions": self.refresh_subscriptions(user=payload.get("user"), broker=payload.get("broker"))}
        if name in {"run_strategies", "process_strategy_jobs"}:
            return {"jobs": self.process_strategy_jobs(limit=int(payload.get("limit", 25)))}
        if name == "place_order":
            return {"job": self.enqueue_strategy_order(payload)}
        if name == "start":
            return {"running": True}
        return {"ignored": True, "command": name}

    def run(self):
        logger.info("Trading worker started")
        trading_event("trading_worker_started", force=True)
        if self.control:
            self.control.heartbeat(state="running")
        while not self._stop_event.is_set():
            if self.control:
                command = self.control.next_pending()
                if command:
                    try:
                        self.control.complete(command["_id"], self.handle_command(command))
                    except Exception as exc:
                        logger.exception("Worker command failed")
                        trading_exception("worker_command_failed", exc, command=command)
                        self.control.complete(command["_id"], error=str(exc))
                now = time.monotonic()
                if self.enable_broker_maintenance:
                    if now - self._last_relogin >= self.relogin_interval_seconds:
                        self.refresh_broker_logins()
                        self._last_relogin = now
                    if now - self._last_subscription >= self.subscription_interval_seconds:
                        self.refresh_subscriptions()
                        self._last_subscription = now
                processed = self.process_strategy_jobs()
                self.control.heartbeat(state="running", processed_jobs=len(processed))
            time.sleep(self.interval_seconds)
        if self.control:
            self.control.heartbeat(state="stopped")
        logger.info("Trading worker stopped")
        trading_event("trading_worker_stopped", force=True)
