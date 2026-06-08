import datetime
from bson import ObjectId


ORDER_STATUSES = {
    "created",
    "submitted",
    "filled",
    "rejected",
    "cancelled",
    "partial_fill",
}


class OrderLifecycleService:
    def __init__(self, db, collection_name="normalized_orders"):
        self.collection = db[collection_name]
        self.collection.create_index([("user", 1), ("created_at", -1)])
        self.collection.create_index([("status", 1), ("updated_at", -1)])

    @staticmethod
    def _now():
        return datetime.datetime.now(datetime.UTC)

    @staticmethod
    def _serialize(order):
        if not order:
            return None
        order = dict(order)
        order["_id"] = str(order["_id"])
        for key in ("created_at", "updated_at"):
            if hasattr(order.get(key), "isoformat"):
                order[key] = order[key].isoformat()
        return order

    def create_order(self, user, broker, symbol, side, quantity, **extra):
        now = self._now()
        order = {
            "user": user,
            "broker": broker,
            "symbol": symbol,
            "side": str(side).upper(),
            "quantity": int(quantity),
            "status": "created",
            "created_at": now,
            "updated_at": now,
            "events": [{"status": "created", "at": now, "data": extra}],
        }
        order.update(extra)
        result = self.collection.insert_one(order)
        return str(result.inserted_id)

    def transition(self, order_id, status, data=None):
        if status not in ORDER_STATUSES:
            raise ValueError(f"Unsupported order status: {status}")
        now = self._now()
        event = {"status": status, "at": now, "data": data or {}}
        result = self.collection.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": status, "updated_at": now}, "$push": {"events": event}},
        )
        if result.matched_count == 0:
            raise ValueError("Order not found")
        return self.get_order(order_id)

    def get_order(self, order_id):
        return self._serialize(self.collection.find_one({"_id": ObjectId(order_id)}))

    def list_orders(self, user, limit=50, status=None):
        query = {"user": user}
        if status:
            query["status"] = status
        rows = self.collection.find(query).sort("created_at", -1).limit(int(limit))
        return [self._serialize(row) for row in rows]
