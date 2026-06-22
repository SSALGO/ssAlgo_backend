import asyncio
from bson import ObjectId
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from connectors.connector import Exchange


class FakeAliceBlueTrade:
    def __init__(self, complete_order_ids=None):
        self.complete_order_ids = set(complete_order_ids or [])

    def get_orderbook(self):
        return {
            "status": "Ok",
            "result": [
                {"brokerOrderId": order_id, "orderStatus": "COMPLETE"}
                for order_id in self.complete_order_ids
            ],
        }


class FakeAliceBlue:
    def __init__(self, responses, complete_order_ids=None):
        self.responses = list(responses)
        self.calls = []
        self.trade = FakeAliceBlueTrade(complete_order_ids)

    def square_off_position(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _exchange(fake_db, alice):
    exchange = object.__new__(Exchange)
    exchange.db = fake_db
    exchange.opositions_collection = fake_db["Opositions"]
    exchange.strategy_collection = fake_db["strategies"]
    exchange.broker_collection = fake_db["brokers"]
    exchange.alice = {"alice": alice}
    return exchange


def _trade(**overrides):
    trade = {
        "user": "alice",
        "botcode": "fractal-1",
        "botname": "fractal hedge",
        "strategy": "FRACTALNUBIATIMEHEDGEORDER",
        "symbol": "NIFTY",
        "status": "paused",
        "position": "in",
        "live": True,
    }
    trade.update(overrides)
    return trade


def _oposition():
    return {
        "_id": ObjectId(),
        "user": "alice",
        "botcode": "fractal-1",
        "symbol": "NIFTY",
        "status": "open",
        "pos": [
            {
                "user": "alice",
                "side": "SELL",
                "optionlot": 65,
                "lot": 1,
                "optiontoken": 24100,
                "optionname": "NIFTY23JUN26C24100",
                "exch": "NFO",
                "live": True,
            },
            {
                "user": "alice",
                "side": "SELL",
                "optionlot": 65,
                "lot": 1,
                "optiontoken": 24101,
                "optionname": "NIFTY23JUN26P24100",
                "exch": "NFO",
                "live": True,
            },
        ],
    }


def test_fractal_hedge_stop_exits_each_sell_leg_once_as_buy(fake_db):
    alice = FakeAliceBlue(
        [
            {"status": "Ok", "result": [{"brokerOrderId": "26061800142568"}]},
            {"status": "Ok", "result": [{"brokerOrderId": "26061800142563"}]},
        ],
        complete_order_ids={"26061800142568", "26061800142563"},
    )
    exchange = _exchange(fake_db, alice)
    fake_db["brokers"].insert_one({"user": "alice", "selectedbroker": "aliceblue"})
    fake_db["strategies"].insert_one(_trade())
    oposition = _oposition()
    fake_db["Opositions"].insert_one(oposition)

    assert exchange._handle_aliceblue_fractal_hedge_exit(_trade(), oposition, "stop")

    assert [(call["transaction_type"].value, call["quantity"]) for call in alice.calls] == [
        ("BUY", 65),
        ("BUY", 65),
    ]
    saved = fake_db["Opositions"].find_one({"_id": oposition["_id"]})
    assert saved["status"] == "close"
    assert {leg["exit_order_status"] for leg in saved["pos"]} == {"complete"}


def test_aliceblue_list_response_preserves_exit_order_ids(fake_db):
    alice = FakeAliceBlue(
        [
            [{"status": "Ok", "result": [{"brokerOrderId": "LIST-PE"}]}],
            [{"status": "Ok", "result": [{"brokerOrderId": "LIST-CE"}]}],
        ],
        complete_order_ids={"LIST-PE", "LIST-CE"},
    )
    exchange = _exchange(fake_db, alice)
    fake_db["strategies"].insert_one(_trade())
    oposition = _oposition()
    fake_db["Opositions"].insert_one(oposition)

    assert exchange._handle_aliceblue_fractal_hedge_exit(
        _trade(),
        oposition,
        "stop",
    )

    saved = fake_db["Opositions"].find_one({"_id": oposition["_id"]})
    assert {leg["exit_order_id"] for leg in saved["pos"]} == {
        "LIST-PE",
        "LIST-CE",
    }
    assert saved["status"] == "close"


def test_repeated_fractal_hedge_stop_does_not_duplicate_exit_orders(fake_db):
    alice = FakeAliceBlue(
        [
            {"status": "Ok", "result": [{"brokerOrderId": "26061800142568"}]},
            {"status": "Ok", "result": [{"brokerOrderId": "26061800142563"}]},
        ],
        complete_order_ids={"26061800142568", "26061800142563"},
    )
    exchange = _exchange(fake_db, alice)
    fake_db["strategies"].insert_one(_trade())
    oposition = _oposition()
    fake_db["Opositions"].insert_one(oposition)

    exchange._handle_aliceblue_fractal_hedge_exit(_trade(), oposition, "stop")
    exchange._handle_aliceblue_fractal_hedge_exit(_trade(), oposition, "stop again")

    assert len(alice.calls) == 2
    saved = fake_db["Opositions"].find_one({"_id": oposition["_id"]})
    assert saved["status"] == "close"


def test_failed_fractal_hedge_exit_does_not_mark_position_closed(fake_db):
    alice = FakeAliceBlue(
        [
            {"status": "Not_ok", "message": "rejected"},
            {"status": "Ok", "result": [{"brokerOrderId": "26061800142563"}]},
        ],
        complete_order_ids={"26061800142563"},
    )
    exchange = _exchange(fake_db, alice)
    fake_db["strategies"].insert_one(_trade())
    oposition = _oposition()
    fake_db["Opositions"].insert_one(oposition)

    assert not exchange._handle_aliceblue_fractal_hedge_exit(_trade(), oposition, "stop")

    saved = fake_db["Opositions"].find_one({"_id": oposition["_id"]})
    assert saved["status"] == "exit_failed"
    assert saved["status"] != "close"


def test_fractal_hedge_entry_group_lock_is_single_winner(fake_db):
    exchange = _exchange(fake_db, FakeAliceBlue([]))
    trade = _trade(status="opened", position="out")
    fake_db["strategies"].insert_one(trade)

    assert exchange._lock_fractal_hedge_entry_group(
        trade,
        "FRACTALNUBIATIMEHEDGEORDER.entry",
    )
    assert not exchange._lock_fractal_hedge_entry_group(
        trade,
        "FRACTALNUBIATIMEHEDGEORDER.entry",
    )

    saved = fake_db["strategies"].find_one({"botcode": trade["botcode"]})
    assert saved["entry_order_state"] == "submitting"

    exchange._set_fractal_fire_state(trade, "attempted")
    saved = fake_db["strategies"].find_one({"botcode": trade["botcode"]})
    assert saved["entry_order_state"] == "submitting"


def test_fractal_entry_group_lock_rejects_strategy_with_active_position(fake_db):
    exchange = _exchange(fake_db, FakeAliceBlue([]))
    trade = _trade(status="opened", position="in")
    fake_db["strategies"].insert_one(trade)

    assert not exchange._lock_fractal_hedge_entry_group(
        trade,
        "FRACTALNUBIATIMEHEDGEORDER.entry",
    )


def test_fractal_hedge_entry_leg_lock_prevents_duplicate_leg_order(fake_db):
    exchange = _exchange(fake_db, FakeAliceBlue([]))
    trade = _trade(status="opened", position="out")
    fake_db["strategies"].insert_one(trade)
    planned_leg = {
        "option": "NIFTY23JUN26C24100",
        "optiontoken": 24100,
    }

    first = exchange._lock_fractal_hedge_entry_leg(
        trade,
        planned_leg,
        "CE_24100",
        "BUY",
        65,
        "FRACTALNUBIATIMEHEDGEORDER.entry",
    )
    second = exchange._lock_fractal_hedge_entry_leg(
        trade,
        planned_leg,
        "CE_24100",
        "BUY",
        65,
        "FRACTALNUBIATIMEHEDGEORDER.entry",
    )

    assert first
    assert second is None


def test_generic_strategy_dispatch_does_not_run_fractal_hedge_entry(fake_db):
    exchange = object.__new__(Exchange)
    exchange._log_strategy_evaluation = MagicMock()
    exchange.FRACTALNUBIATIMEHEDGEORDER = MagicMock()

    exchange.process_strategy(_trade(status="opened", position="out"))

    exchange.FRACTALNUBIATIMEHEDGEORDER.assert_not_called()


def test_fractal_reset_keeps_exit_pending_position_active(fake_db, monkeypatch):
    from app.api.legacy_compat import common

    fake_db["strategies"].insert_one(
        _trade(status="paused", position="out", entry_order_state="submitted")
    )
    fake_db["Opositions"].insert_one({
        "user": "alice",
        "botcode": "fractal-1",
        "status": "exit_pending",
        "pos": [],
    })
    monkeypatch.setattr(
        common,
        "collection",
        lambda name: fake_db[name],
    )

    update = common.fractal_reset_update(
        "fractal-1",
        "alice",
        {"status": "opened"},
    )

    assert update["$set"]["position"] == "in"
    assert "$unset" not in update


def test_start_rejects_unresolved_fractal_exit(fake_db, monkeypatch):
    from app.api.legacy_compat import strategies

    fake_db["strategies"].insert_one(
        _trade(status="paused", position="out")
    )
    fake_db["Opositions"].insert_one({
        "_id": ObjectId(),
        "user": "alice",
        "botcode": "fractal-1",
        "status": "exit_failed",
        "pos": [],
    })

    async def fake_payload(_request):
        return {"id": "fractal-1"}

    monkeypatch.setattr(strategies, "get_database", lambda: fake_db)
    monkeypatch.setattr(strategies, "payload_from_request", fake_payload)
    monkeypatch.setattr(
        strategies,
        "WorkerControlService",
        lambda _db: SimpleNamespace(
            get_status=lambda: {
                "healthy": True,
                "strategy_engine": "running",
            }
        ),
    )
    monkeypatch.setattr(strategies, "trading_event", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            strategies.api_start_ssalgo(
                object(),
                user={"username": "alice"},
            )
        )

    assert error.value.status_code == 409
    assert error.value.detail["position_status"] == "exit_failed"
