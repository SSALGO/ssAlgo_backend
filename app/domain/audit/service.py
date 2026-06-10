import datetime
import csv
import io
import json
from typing import Any

from bson import ObjectId


SENSITIVE_KEYS = {
    "password",
    "pwd",
    "pin",
    "api_secret",
    "apisecret",
    "secret_key",
    "totp",
    "totp_key",
    "factor2",
    "access_token",
    "auth_code",
    "interactive_secret",
    "epassword",
    "token",
    "reset_token",
    "otp",
    "signature",
}


def _jsonable(value: Any):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(mask_value(key, item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def mask_value(key: str, value: Any):
    if str(key).lower() in SENSITIVE_KEYS:
        return "***"
    return value


class AuditLogService:
    def __init__(self, db=None):
        self.db = db
        self.collection = db["audit_logs"] if db is not None else None
        if self.collection is not None:
            self.collection.create_index([("created_at", -1)])
            self.collection.create_index([("user", 1), ("created_at", -1)])
            self.collection.create_index([("event", 1), ("created_at", -1)])
            self.collection.create_index([("resource_type", 1), ("resource_id", 1)])

    @staticmethod
    def now():
        return datetime.datetime.now(datetime.UTC)

    def record(
        self,
        event: str,
        user: str = "",
        resource_type: str = "",
        resource_id: str = "",
        status: str = "success",
        details: dict | None = None,
        actor: str = "",
    ):
        if self.collection is None:
            return None
        row = {
            "event": event,
            "user": user or "",
            "actor": actor or user or "",
            "resource_type": resource_type or "",
            "resource_id": str(resource_id or ""),
            "status": status,
            "details": _jsonable(details or {}),
            "created_at": self.now(),
        }
        result = self.collection.insert_one(row)
        row["_id"] = str(result.inserted_id)
        return row

    @staticmethod
    def _serialize(row):
        if not row:
            return None
        row = dict(row)
        if "_id" in row:
            row["_id"] = str(row["_id"])
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
        return row

    def list_events(self, user="", event="", date_from=None, date_to=None, limit=100):
        if self.collection is None:
            return []
        query = {}
        if user:
            query["user"] = user
        if event:
            query["event"] = event
        created_filter = {}
        if date_from:
            created_filter["$gte"] = date_from
        if date_to:
            created_filter["$lte"] = date_to
        if created_filter:
            query["created_at"] = created_filter
        rows = self.collection.find(query).sort("created_at", -1).limit(int(limit))
        return [self._serialize(row) for row in rows]

    def export_csv(self, user="", event="", date_from=None, date_to=None, limit=1000):
        rows = self.list_events(user=user, event=event, date_from=date_from, date_to=date_to, limit=limit)
        handle = io.StringIO()
        writer = csv.DictWriter(handle, fieldnames=["created_at", "event", "status", "user", "actor", "resource_type", "resource_id", "details"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "created_at": row.get("created_at", ""),
                "event": row.get("event", ""),
                "status": row.get("status", ""),
                "user": row.get("user", ""),
                "actor": row.get("actor", ""),
                "resource_type": row.get("resource_type", ""),
                "resource_id": row.get("resource_id", ""),
                "details": json.dumps(row.get("details", {}), sort_keys=True),
            })
        return handle.getvalue()

    def prune_older_than(self, days: int):
        if self.collection is None:
            return 0
        cutoff = self.now() - datetime.timedelta(days=int(days))
        if hasattr(self.collection, "delete_many"):
            return self.collection.delete_many({"created_at": {"$lt": cutoff}}).deleted_count
        deleted = 0
        for row in list(self.collection.find({"created_at": {"$lt": cutoff}})):
            deleted += self.collection.delete_one({"_id": row["_id"]}).deleted_count
        return deleted
