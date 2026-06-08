import datetime
from typing import Dict


class WorkerControlService:
    def __init__(self, db):
        self.db = db
        self.commands = db["worker_commands"]
        self.status = db["worker_status"]
        self.commands.create_index([("status", 1), ("created_at", 1)])
        self.status.create_index("name", unique=True)

    @staticmethod
    def now():
        return datetime.datetime.now(datetime.UTC)

    def enqueue(self, command: str, user: str, payload: Dict | None = None):
        row = {
            "command": command,
            "user": user,
            "payload": payload or {},
            "status": "pending",
            "created_at": self.now(),
            "updated_at": self.now(),
        }
        result = self.commands.insert_one(row)
        row["_id"] = str(result.inserted_id)
        return row

    def next_pending(self):
        row = self.commands.find_one({"status": "pending"})
        if not row:
            return None
        self.commands.update_one(
            {"_id": row["_id"]},
            {"$set": {"status": "processing", "updated_at": self.now()}},
        )
        return row

    def complete(self, command_id, result=None, error=""):
        status = "failed" if error else "completed"
        self.commands.update_one(
            {"_id": command_id},
            {"$set": {"status": status, "result": result or {}, "error": error, "updated_at": self.now()}},
        )

    def heartbeat(self, name="trading_worker", **fields):
        payload = {"name": name, "heartbeat_at": self.now(), **fields}
        self.status.update_one({"name": name}, {"$set": payload}, upsert=True)
        return payload

    def get_status(self, name="trading_worker"):
        row = self.status.find_one({"name": name}) or {"name": name, "state": "unknown"}
        row = dict(row)
        if "_id" in row:
            row["_id"] = str(row["_id"])
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
        return row
