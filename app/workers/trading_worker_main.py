import logging
import os
import signal
import time

from app.core.database import get_database
from app.core.logging_config import configure_logging
from app.core.trading_debug import configure_trading_debug_logging
from app.domain.brokers.health import BrokerHealthService
from app.workers.trading_worker import TradingWorker


logger = logging.getLogger(__name__)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main():
    configure_logging()
    if _env_bool("DEBUG_TRADING", False) or _env_bool("SSLAGO_DEBUG_TRADING", False):
        configure_trading_debug_logging()
    db = get_database()
    worker = TradingWorker(db=db, health_service=BrokerHealthService(db))
    strategy_runtime = None
    if _env_bool("SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE", True):
        from exchangeload import start_trader

        strategy_runtime = start_trader()
        worker.control.heartbeat(state="starting", strategy_engine="starting", strategy_engine_error="")
    else:
        worker.control.heartbeat(state="running", strategy_engine="disabled", strategy_engine_error="")

    def stop_worker(_signum, _frame):
        worker.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    worker.start()
    if strategy_runtime:
        strategy_runtime.wait_ready(timeout=180)
        runtime_status = strategy_runtime.status()
        worker.control.heartbeat(
            state="running" if runtime_status["state"] == "running" else "degraded",
            strategy_engine=runtime_status["state"],
            strategy_engine_error=runtime_status["error"],
        )
        if runtime_status["state"] != "running":
            logger.error("Strategy engine failed to start: %s", runtime_status["error"])
    while worker._thread and worker._thread.is_alive():
        time.sleep(1)


if __name__ == "__main__":
    main()
