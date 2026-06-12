import logging
import os
import signal
import time
from urllib.parse import urlsplit

from app.core.config import AppConfig
from app.core.database import get_database
from app.core.logging_config import configure_logging
from app.core.trading_debug import configure_trading_debug_logging, trading_event
from app.domain.brokers.health import BrokerHealthService
from app.workers.trading_worker import TradingWorker


logger = logging.getLogger(__name__)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _log_position_recovery_state(db):
    open_positions = list(db["Opositions"].find(
        {"status": "open"},
        {"botcode": 1, "user": 1, "symbol": 1, "optionname": 1, "decision": 1},
    ))
    if not open_positions:
        logger.info("Position recovery scan: no open positions")
        return

    recovery_rows = []
    for position in open_positions:
        strategy = db["strategies"].find_one(
            {
                "botcode": position.get("botcode"),
                "user": position.get("user"),
            },
            {"status": 1, "position": 1},
        )
        recovery_rows.append({
            "user": position.get("user"),
            "botcode": position.get("botcode"),
            "symbol": position.get("symbol"),
            "optionname": position.get("optionname"),
            "decision": position.get("decision"),
            "strategy_status": strategy.get("status") if strategy else "missing",
            "strategy_position": strategy.get("position") if strategy else "missing",
        })

    logger.warning(
        "Position recovery scan: %s open position(s) will remain under exit "
        "management even when their strategy is stopped: %s",
        len(open_positions),
        recovery_rows[:25],
    )


def _mongo_identity():
    parsed = urlsplit(AppConfig.MONGO_URI)
    return {
        "host": parsed.hostname or "unknown",
        "database": AppConfig.MONGO_DB,
    }


def main():
    configure_logging()
    debug_trading = (
        _env_bool("DEBUG_TRADING", False)
        or _env_bool("SSLAGO_DEBUG_TRADING", False)
    )
    legacy_engine_enabled = _env_bool("SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE", True)
    if debug_trading:
        configure_trading_debug_logging()

    logger.info(
        "Trading runtime startup: legacy_engine=%s debug_trading=%s",
        legacy_engine_enabled,
        debug_trading,
    )
    db = get_database()
    try:
        db.command("ping")
    except Exception:
        logger.exception("Trading runtime startup failed: MongoDB ping failed")
        raise

    if db.name != AppConfig.MONGO_DB:
        raise RuntimeError(
            f"MongoDB mismatch: connected database={db.name}, "
            f"configured database={AppConfig.MONGO_DB}"
        )

    mongo_identity = _mongo_identity()
    logger.info(
        "MongoDB ping succeeded: host=%s database=%s shared_runtime_database=true",
        mongo_identity["host"],
        mongo_identity["database"],
    )
    _log_position_recovery_state(db)
    worker = TradingWorker(db=db, health_service=BrokerHealthService(db))
    strategy_runtime = None
    if legacy_engine_enabled:
        from exchangeload import start_trader

        strategy_runtime = start_trader(database=db)
        worker.control.heartbeat(state="starting", strategy_engine="starting", strategy_engine_error="")
    else:
        worker.control.heartbeat(state="running", strategy_engine="disabled", strategy_engine_error="")

    def stop_worker(_signum, _frame):
        worker.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    worker.start()
    if strategy_runtime:
        startup_timeout = _env_int("SSLAGO_STRATEGY_ENGINE_STARTUP_TIMEOUT", 180)
        ready = strategy_runtime.wait_ready(timeout=startup_timeout)
        runtime_status = strategy_runtime.status()
        if not ready and runtime_status["state"] == "starting":
            runtime_status = {
                "state": "failed",
                "error": (
                    "Strategy engine initialization timed out after "
                    f"{startup_timeout} seconds"
                ),
            }
        worker.control.heartbeat(
            state="running" if runtime_status["state"] == "running" else "degraded",
            strategy_engine=runtime_status["state"],
            strategy_engine_error=runtime_status["error"],
        )
        if runtime_status["state"] == "running":
            trading_event("strategy_engine_ready", force=True)
        else:
            trading_event(
                "strategy_engine_failed",
                force=True,
                state=runtime_status["state"],
                error=runtime_status["error"],
            )
            logger.error("Strategy engine failed to start: %s", runtime_status["error"])
            worker.stop()
            raise RuntimeError(
                f"Strategy engine failed to start: {runtime_status['error']}"
            )
    while worker._thread and worker._thread.is_alive():
        time.sleep(1)


if __name__ == "__main__":
    main()
