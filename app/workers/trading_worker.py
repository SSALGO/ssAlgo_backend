import logging
import threading
import time

from app.workers.control import WorkerControlService


class TradingWorker:
    """Process-safe placeholder for moving trading loops outside Flask."""

    def __init__(self, health_service=None, db=None, interval_seconds=1):
        self.health_service = health_service
        self.db = db
        self.control = WorkerControlService(db) if db is not None else None
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run(self):
        logging.info("Trading worker started")
        if self.control:
            self.control.heartbeat(state="running")
        while not self._stop_event.is_set():
            if self.control:
                command = self.control.next_pending()
                if command:
                    try:
                        if command.get("command") == "stop":
                            self.control.complete(command["_id"], {"stopping": True})
                            self._stop_event.set()
                            break
                        self.control.complete(command["_id"], {"ignored": True})
                    except Exception as exc:
                        self.control.complete(command["_id"], error=str(exc))
                self.control.heartbeat(state="running")
            time.sleep(self.interval_seconds)
        if self.control:
            self.control.heartbeat(state="stopped")
        logging.info("Trading worker stopped")
