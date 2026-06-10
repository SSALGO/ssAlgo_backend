import datetime

from app.domain.audit.service import AuditLogService, _jsonable
from app.domain.brokers.adapters import BrokerAdapterFactory, BrokerCredentials
from app.domain.orders.lifecycle import OrderLifecycleService


class BrokerReconciliationService:
    def __init__(self, db, adapter_factory=None, audit_service=None):
        self.db = db
        self.audit = audit_service or AuditLogService(db)
        self.adapter_factory = adapter_factory or BrokerAdapterFactory(
            db=db,
            order_lifecycle=OrderLifecycleService(db, audit_service=self.audit),
            risk_service=None,
        )

    @staticmethod
    def _status(success, message="", data=None):
        return {"success": bool(success), "message": message, "data": data or {}}

    def smoke_test(self, user: str, broker: str, symbol: str = ""):
        adapter = self.adapter_factory.create(broker)
        result = {"broker": broker, "user": user, "checks": {}}
        try:
            login = adapter.login(BrokerCredentials(user=user, broker=broker))
            result["checks"]["login"] = login
            if login.get("success") is False:
                raise RuntimeError(login.get("message") or "Broker login failed")
            result["checks"]["funds"] = adapter.funds(user)
            result["checks"]["positions"] = adapter.positions(user)
            if symbol:
                result["checks"]["quote"] = adapter.quote(symbol, user=user)
            result["success"] = True
            status = "success"
        except Exception as exc:
            result["success"] = False
            result["error"] = str(exc)
            status = "failed"
        self.audit.record(
            "broker_smoke_test",
            user=user,
            resource_type="broker",
            resource_id=broker,
            status=status,
            details=result,
        )
        return _jsonable(result)

    def reconcile_positions(self, user: str, broker: str):
        adapter = self.adapter_factory.create(broker)
        result = {"broker": broker, "user": user, "checked_at": datetime.datetime.now(datetime.UTC)}
        try:
            adapter.login(BrokerCredentials(user=user, broker=broker))
            broker_positions = adapter.positions(user)
            local_positions = [
                dict(row)
                for row in self.db["paper_positions"].find({"user": user, "broker": broker, "net_quantity": {"$ne": 0}})
            ]
            result.update({
                "success": True,
                "broker_positions": broker_positions,
                "local_open_position_count": len(local_positions),
                "local_positions": local_positions,
            })
            status = "success"
        except Exception as exc:
            result.update({"success": False, "error": str(exc)})
            status = "failed"
        self.db["broker_reconciliations"].insert_one({**result, "created_at": datetime.datetime.now(datetime.UTC)})
        self.audit.record(
            "broker_position_reconciliation",
            user=user,
            resource_type="broker",
            resource_id=broker,
            status=status,
            details=result,
        )
        return _jsonable(result)
