import datetime

from app.domain.brokers.registry import BROKER_REQUIREMENTS, BROKER_STATUS


SECRET_FIELD_NAMES = {
    "password",
    "pwd",
    "pin",
    "api_secret",
    "apisecret",
    "secret_key",
    "totp_key",
    "factor2",
    "access_token",
    "auth_code",
    "interactive_secret",
    "epassword",
}


class BrokerHealthService:
    def __init__(self, db):
        self.db = db
        self.health_collection = db["broker_health"]
        self.apis_collection = db["apis"]
        self.broker_collection = db["broker"]
        self.health_collection.create_index([("user", 1), ("broker", 1)], unique=True)

    @staticmethod
    def _now():
        return datetime.datetime.utcnow()

    @staticmethod
    def _serialize(row):
        if not row:
            return None
        row = dict(row)
        if "_id" in row:
            row["_id"] = str(row["_id"])
        for key in ("updated_at", "token_expires_at", "last_quote_time"):
            if hasattr(row.get(key), "isoformat"):
                row[key] = row[key].isoformat()
        return row

    def required_fields(self, broker):
        return [field["id"] for field in BROKER_REQUIREMENTS.get(broker, [])]

    def missing_credentials(self, username, broker):
        if broker == "paper":
            return []
        api = self.apis_collection.find_one({"user": username, "broker": broker}) or {}
        return [
            field
            for field in self.required_fields(broker)
            if not str(api.get(field, "")).strip()
        ]

    def credential_summary(self, username, broker):
        api = self.apis_collection.find_one({"user": username, "broker": broker}) or {}
        summary = {}
        for field in self.required_fields(broker):
            value = api.get(field)
            if field in SECRET_FIELD_NAMES:
                summary[field] = bool(value)
            else:
                summary[field] = value if value else ""
        return summary

    def update_health(self, username, broker, **fields):
        now = self._now()
        payload = {"updated_at": now}
        payload.update(fields)
        self.health_collection.update_one(
            {"user": username, "broker": broker},
            {"$set": payload, "$setOnInsert": {"user": username, "broker": broker, "created_at": now}},
            upsert=True,
        )
        return self.get_health(username, broker)

    def get_health(self, username, broker):
        row = self.health_collection.find_one({"user": username, "broker": broker}) or {}
        missing = self.missing_credentials(username, broker)
        registry_status = BROKER_STATUS.get(broker, {})
        active = (self.broker_collection.find_one({"user": username}) or {}).get("selectedbroker")
        status = dict(row)
        status.update(
            {
                "user": username,
                "broker": broker,
                "active": active == broker,
                "enabled": registry_status.get("enabled", True),
                "registry_status": registry_status.get("status", "wired"),
                "notes": registry_status.get("notes", ""),
                "missing_credentials": missing,
                "credential_summary": self.credential_summary(username, broker),
                "login_status": row.get("login_status") or ("missing_credentials" if missing else "unknown"),
                "websocket_status": row.get("websocket_status") or "unknown",
                "token_expires_at": row.get("token_expires_at"),
                "last_quote_time": row.get("last_quote_time"),
                "last_order_result": row.get("last_order_result"),
                "last_error": row.get("last_error"),
            }
        )
        return self._serialize(status)

    def list_health(self, username):
        brokers = list(BROKER_STATUS.keys())
        if "paper" not in brokers:
            brokers.insert(0, "paper")
        return [self.get_health(username, broker) for broker in brokers]
