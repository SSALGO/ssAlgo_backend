import signal
import time

from app.core.database import get_database
from app.domain.brokers.health import BrokerHealthService
from app.workers.trading_worker import TradingWorker


def main():
    db = get_database()
    worker = TradingWorker(db=db, health_service=BrokerHealthService(db))

    def stop_worker(_signum, _frame):
        worker.stop()

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)
    worker.start()
    while worker._thread and worker._thread.is_alive():
        time.sleep(1)


if __name__ == "__main__":
    main()
