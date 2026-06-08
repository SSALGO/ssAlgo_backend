import datetime
from uuid import uuid4

from .base import BrokerAdapter


class PaperBrokerAdapter(BrokerAdapter):
    broker_name = "paper"

    def __init__(
        self,
        db=None,
        health_service=None,
        order_lifecycle=None,
        risk_service=None,
        slippage_bps=2.0,
        brokerage_bps=1.0,
    ):
        super().__init__(
            db=db,
            health_service=health_service,
            order_lifecycle=order_lifecycle,
            risk_service=risk_service,
        )
        self.slippage_bps = float(slippage_bps)
        self.brokerage_bps = float(brokerage_bps)
        self.positions_collection = db["paper_positions"] if db is not None else None
        self.quotes_collection = db["paper_quotes"] if db is not None else None

    def login(self, credentials):
        if self.health_service:
            self.health_service.update_health(credentials.user, self.broker_name, login_status="connected")
        return {"success": True, "broker": self.broker_name, "mode": "paper"}

    def _execution_price(self, order):
        raw_price = order.price or order.metadata.get("ltp") or order.metadata.get("last_price") or 0
        price = float(raw_price)
        if price <= 0:
            price = 1.0
        direction = 1 if str(order.side).upper() == "BUY" else -1
        return round(price * (1 + direction * self.slippage_bps / 10000), 4)

    def _brokerage(self, price, quantity):
        return round(abs(float(price) * int(quantity)) * self.brokerage_bps / 10000, 4)

    def _update_position(self, order, fill_price):
        if self.positions_collection is None:
            return
        side = str(order.side).upper()
        signed_qty = int(order.quantity) if side == "BUY" else -int(order.quantity)
        now = datetime.datetime.now(datetime.UTC)
        self.positions_collection.update_one(
            {"user": order.user, "symbol": order.symbol, "broker": self.broker_name},
            {
                "$inc": {"net_quantity": signed_qty},
                "$set": {"last_price": fill_price, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def place_order(self, order):
        self.check_risk(order, mode="paper")
        broker_order_id = f"paper-{uuid4().hex[:12]}"
        fill_price = self._execution_price(order)
        brokerage = self._brokerage(fill_price, order.quantity)
        order_id = None
        if self.order_lifecycle:
            order_id = self.order_lifecycle.create_order(
                user=order.user,
                broker=self.broker_name,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                strategy_id=order.strategy_id,
                mode="paper",
                requested_price=order.price,
                order_type=order.order_type,
            )
            self.order_lifecycle.transition(order_id, "submitted", {"broker_order_id": broker_order_id})
            self.order_lifecycle.transition(
                order_id,
                "filled",
                {
                    "broker_order_id": broker_order_id,
                    "fill_price": fill_price,
                    "brokerage": brokerage,
                },
            )
        self._update_position(order, fill_price)
        if self.health_service:
            self.health_service.update_health(
                order.user,
                self.broker_name,
                login_status="connected",
                last_order_result={"success": True, "broker_order_id": broker_order_id},
            )
        return {
            "success": True,
            "broker": self.broker_name,
            "broker_order_id": broker_order_id,
            "order_id": order_id,
            "status": "filled",
            "fill_price": fill_price,
            "brokerage": brokerage,
        }

    def cancel_order(self, user, order_id):
        if self.order_lifecycle:
            return self.order_lifecycle.transition(order_id, "cancelled", {"cancelled_by": user})
        return {"success": True, "order_id": order_id, "status": "cancelled"}

    def positions(self, user):
        if self.positions_collection is None:
            return []
        rows = self.positions_collection.find({"user": user, "broker": self.broker_name})
        result = []
        for row in rows:
            row["_id"] = str(row["_id"])
            result.append(row)
        return result

    def funds(self, user):
        return {"cash": None, "mode": "paper", "user": user}

    def quote(self, symbol, **kwargs):
        price = kwargs.get("price") or kwargs.get("ltp")
        if not price and self.quotes_collection is not None:
            row = self.quotes_collection.find_one({"symbol": symbol})
            price = row.get("ltp") if row else None
        return {"symbol": symbol, "ltp": float(price or 0), "broker": self.broker_name}

    def subscribe(self, symbols, **kwargs):
        return {"success": True, "broker": self.broker_name, "symbols": list(symbols or [])}
