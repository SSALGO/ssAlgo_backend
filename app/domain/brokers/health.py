import datetime

from app.domain.brokers.registry import (
    BROKER_REQUIREMENTS,
    BROKER_STATUS,
    broker_lookup_ids,
    normalize_broker_id,
)


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
    "alice_password",
    "sessionid",
    "session_id",
    "user_session",
}


def is_secret_field(field_name):
    return str(field_name or "").lower() in SECRET_FIELD_NAMES


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
        for key in ("updated_at", "token_expires_at", "last_quote_time", "last_test_at"):
            if hasattr(row.get(key), "isoformat"):
                row[key] = row[key].isoformat()
        return row

    @staticmethod
    def _serialize_value(value):
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    def required_fields(self, broker):
        return [field["id"] for field in BROKER_REQUIREMENTS.get(normalize_broker_id(broker), [])]

    def credential_row(self, username, broker):
        return self.apis_collection.find_one({
            "user": username,
            "broker": {"$in": broker_lookup_ids(broker)},
        }) or {}

    def active_broker(self, username):
        broker_row = self.broker_collection.find_one({"user": username}) or {}
        selected = broker_row.get("selectedbroker") or broker_row.get("selected_broker")
        if selected:
            return normalize_broker_id(selected)

        legacy_selected = self.apis_collection.find_one(
            {"user": username, "selected_broker": {"$exists": True, "$ne": ""}}
        ) or {}
        selected = legacy_selected.get("selected_broker") or legacy_selected.get("selectedbroker")
        if selected:
            return normalize_broker_id(selected)

        api_row = self.apis_collection.find_one({"user": username, "broker": {"$exists": True, "$ne": ""}}) or {}
        return normalize_broker_id(api_row.get("broker")) or "paper"

    def missing_credentials(self, username, broker):
        if broker == "paper":
            return []
        api = self.credential_row(username, broker)
        return [
            field
            for field in self.required_fields(broker)
            if not str(api.get(field, "")).strip()
        ]

    def credential_summary(self, username, broker):
        api = self.credential_row(username, broker)
        summary = {}
        for field in self.required_fields(broker):
            value = api.get(field)
            if is_secret_field(field):
                summary[field] = bool(value)
            else:
                summary[field] = value if value else ""
        return summary

    def _masked_credentials(self, row):
        if not row:
            return {}
        cleaned = {}
        secret_present = {}
        for key, value in dict(row).items():
            if key == "_id":
                cleaned["_id"] = str(value)
                cleaned["id"] = cleaned.get("id") or str(value)
                continue
            if is_secret_field(key):
                cleaned[key] = ""
                secret_present[key] = bool(str(value or "").strip())
                continue
            cleaned[key] = self._serialize_value(value)
        if secret_present:
            cleaned["secret_present"] = secret_present
        return cleaned

    def saved_credentials(self, username):
        credentials = {}
        for row in self.apis_collection.find({"user": username}):
            broker = normalize_broker_id(
                row.get("broker") or row.get("selected_broker") or row.get("selectedbroker")
            )
            if not broker:
                continue
            credentials[broker] = self._masked_credentials(row)
        return credentials

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
        active = self.active_broker(username)
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
                "login_status": row.get("login_status") or ("missing_credentials" if missing else "not_tested"),
                "websocket_status": row.get("websocket_status") or ("missing_credentials" if missing else "not_tested"),
                "token_expires_at": row.get("token_expires_at"),
                "last_quote_time": row.get("last_quote_time"),
                "last_test_at": row.get("last_test_at"),
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
