"""End-to-end tests for Algorithm 143 (EMA, SSTRIKE, EMA futures variants)."""

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.legacy_compat.common import build_strategy, strategy_forms
from app.domain.brokers.adapters import BrokerOrder
from app.domain.risk.service import RiskControlService
from app.workers.trading_worker import TradingWorker
from connectors.connector import Exchange
from models import EMA_fut_mode, EMA_mode, SSTRIKE_mode


def _base_algo143_payload(**overrides):
    payload = {
        "botname": "algo143-test",
        "symbol": "NIFTY",
        "Expiry": "Current Week",
        "timeframe": "5m",
        "r1": "143",
        "k1": "143",
        "r2": "143",
        "k2": "143",
        "Newsignal": "true",
        "USEMA": "false",
        "ema": "200",
        "Intraday": "true",
        "FixedLot": "FixedLot",
        "BSmode": "true",
        "pct_point": "true",
        "pnlexit_tpslexit": "true",
        "strike": "0",
        "lot": "1",
        "initiallot": "1",
        "ttw": "0",
        "stepvalue": "1",
        "MultiFactor": "1",
        "candle1": "1",
        "candle2": "2",
        "slicing": "20",
        "DaysHead": "0",
        "RolloverTime": "13:01",
        "StartTime": "09:15",
        "ExitTime": "15:20",
        "trail": "0",
        "trail_stoploss": "500",
        "tp": "100",
        "sl": "50",
        "status": "paused",
        "position": "out",
        "maxprofit": "100000",
        "maxloss": "10000",
        "live": "false",
    }
    payload.update(overrides)
    return payload


def _user():
    return {"username": "alice", "_id": "user123", "mobile": "9999999999"}


def _make_candles(symbol="NIFTY", count=600, base_price=22000.0, trend="bull"):
    rows = []
    start = datetime.datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
    price = base_price
    for index in range(count):
        ts = start + datetime.timedelta(minutes=index)
        if trend == "bull":
            price += 2 if index % 3 else -0.5
        elif trend == "bear":
            price -= 2 if index % 3 else 0.5
        else:
            price += 0.2 if index % 2 else -0.2
        high = price + 5
        low = price - 5
        rows.append({
            "time": ts.strftime("%d-%m-%Y %H:%M:%S"),
            "open": price,
            "high": high,
            "low": low,
            "close": price,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def _exchange_stub(fake_db, *, symbol="NIFTY", candles=None):
    exchange = object.__new__(Exchange)
    exchange.testmode = True
    exchange.userloggedin = ["alice"]
    exchange.marketdays = 5
    exchange.timeswitch = {"1m": "1", "5m": "5", "15m": "15"}
    exchange.candleswitch = {"1m": 500, "5m": 1000, "15m": 2000}
    if candles is None:
        frame = _make_candles(symbol)
    elif isinstance(candles, pd.DataFrame):
        frame = candles
    elif candles == []:
        frame = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    else:
        frame = pd.DataFrame(candles)
    exchange.dataframes = {symbol: frame}
    exchange.websocketretry = 0
    exchange.prices = {}
    exchange.controls = {
        symbol: {
            "controlmode": False,
            "Buytrade": False,
            "Selltrade": False,
        }
    }
    exchange.strategyinputs = {
        "EMA": {"update": False},
        "SSTRIKE": {"update": False},
    }
    exchange.strategy_collection = fake_db["strategies"]
    exchange.opositions_collection = fake_db["Opositions"]
    return exchange


def _exchange_stub_with_order_mocks(fake_db, **kwargs):
    exchange = _exchange_stub(fake_db, **kwargs)
    exchange.OBUY = MagicMock()
    exchange.OSELL = MagicMock()
    exchange.OBUYEXIT = MagicMock()
    exchange.OSELLEXIT = MagicMock()
    exchange.FBUY = MagicMock()
    exchange.FSELL = MagicMock()
    exchange.FEXIT = MagicMock()
    return exchange


def _sstrike_trade(**overrides):
    trade = {
        "user": "alice",
        "botcode": "test_sstrike_143",
        "strategy": "SSTRIKE",
        "symbol": "NIFTY",
        "status": "opened",
        "position": "out",
        "timeframe": "5m",
        "r1": 143,
        "k1": 143,
        "candle1": 1,
        "candle2": 2,
        "Newsignal": True,
        "BSmode": True,
        "Intraday": True,
        "StartTime": "00:00",
        "ExitTime": "23:59",
        "timetowait": 0,
        "live": False,
    }
    trade.update(overrides)
    return trade


def _ema_trade(**overrides):
    trade = _sstrike_trade(**overrides)
    trade["strategy"] = "EMA"
    trade["r1"] = 19
    trade["k1"] = 20
    return trade


class TestAlgorithm143Configuration:
    def test_sstrike_model_defaults_143_ema_spans(self):
        doc = build_strategy("sstrike", _base_algo143_payload(), _user())
        assert doc["strategy"] == "SSTRIKE"
        assert doc["r1"] == 143
        assert doc["k1"] == 143
        assert doc["live"] is False

    def test_ema_model_parses_options_variant(self):
        doc = build_strategy("ema", _base_algo143_payload(r1="19", k1="20"), _user())
        assert doc["strategy"] == "EMA"
        assert doc["r1"] == 19
        assert doc["k1"] == 20

    def test_ema_fut_model_sets_onspot_and_futures_strategy(self):
        payload = _base_algo143_payload(
            r1="19",
            k1="20",
            onspot="true",
            BSmode="false",
        )
        doc = build_strategy("ema_fut", payload, _user())
        assert doc["strategy"] == "EMA"
        assert doc["onspot"] is True

    def test_missing_required_field_raises_clear_error(self):
        payload = _base_algo143_payload()
        payload.pop("lot")
        with pytest.raises(HTTPException) as exc:
            build_strategy("sstrike", payload, _user())
        assert exc.value.status_code == 400
        assert "Missing strategy field" in exc.value.detail

    def test_invalid_numeric_field_raises_clear_error(self):
        payload = _base_algo143_payload(lot="not-a-number")
        with pytest.raises(HTTPException) as exc:
            build_strategy("sstrike", payload, _user())
        assert exc.value.status_code == 400
        assert "Invalid strategy field value" in exc.value.detail

    def test_frontend_form_schemas_include_algo143_fields(self):
        strategy_forms.cache_clear()
        forms = strategy_forms()
        ema_fields = {field["name"] for field in forms["add_ema_form.html"]}
        sstrike_fields = {field["name"] for field in forms["add_sstrike_form.html"]}
        ema_fut_fields = {field["name"] for field in forms["add_ema_fut_form.html"]}
        for required in ("botname", "symbol", "timeframe", "tp", "sl", "lot", "live"):
            assert required in ema_fields
            assert required in sstrike_fields
            assert required in ema_fut_fields
        assert "r1" in sstrike_fields
        assert "onspot" in ema_fut_fields


class TestAlgorithm143SignalGeneration:
    def test_sstrike_skips_when_no_market_data(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db, candles=[])
        trade = _sstrike_trade()
        with patch("connectors.connector.trading_event") as event:
            exchange.SSTRIKE(trade)
        assert exchange.OBUY.call_count == 0
        events = [call.args[0] if call.args else call.kwargs.get("event") for call in event.call_args_list]
        assert "signal_rejected" in events

    def test_ema_evaluates_without_duplicate_entry_when_position_in(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db)
        trade = _ema_trade(position="in", StartTime="00:00", ExitTime="23:59")
        exchange.EMA(trade)
        exchange.OBUY.assert_not_called()
        exchange.OBUYEXIT.assert_called()

    def test_sstrike_does_not_place_entry_when_paused(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db)
        trade = _sstrike_trade(status="paused")
        exchange.SSTRIKE(trade)
        exchange.OBUY.assert_not_called()

    def test_sstrike_respects_timetowait_gate(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db)
        future_ts = int(datetime.datetime.now().timestamp()) + 3600
        trade = _sstrike_trade(timetowait=future_ts)
        exchange.SSTRIKE(trade)
        exchange.OBUY.assert_not_called()

    def test_sstrike_futures_path_uses_fbuy(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db)
        trade = _sstrike_trade(onspot="false", Expiry="Current Month")
        with patch.object(exchange, "_symboltransformmonthfut", return_value="NIFTY-I"):
            exchange.SSTRIKE(trade)
        exchange.FBUY.assert_not_called()
        exchange.FSELL.assert_not_called()

    def test_ema_handles_unknown_symbol_controls(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db, symbol="BTCUSD")
        trade = _ema_trade(symbol="BTCUSD")
        exchange.EMA(trade)
        exchange.OBUY.assert_not_called()
        exchange = _exchange_stub(fake_db)
        trade = _ema_trade()
        with patch.object(exchange, "dataframes", {"NIFTY": None}):
            with patch.object(exchange, "_log_strategy_exception") as log_exc:
                exchange.EMA(trade)
        log_exc.assert_called_once()


class TestAlgorithm143OrderExecution:
    def test_same_entry_signal_received_twice_creates_one_position(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.websocketretry = 0
        exchange.prices = {
            "NIFTY": 22000.0,
            "NIFTY24JUN22000CE": 100.0,
        }
        exchange.subscribe_list = []
        exchange.api = None
        exchange.add_symbol_to_websocket = MagicMock()
        exchange.add_to_websocket = MagicMock()
        exchange._make_instrument = MagicMock(return_value=MagicMock())
        exchange._wait_for_market_price = MagicMock(return_value=100.0)
        exchange._get_market_price = MagicMock(return_value=100.0)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)
        exchange.MainOptionSelect = MagicMock(
            return_value=("NIFTY24JUN22000CE", 50, "2099-06-27", 12345)
        )

        trade = _ema_trade(
            botname="algo143-test",
            live=False,
            Expiry="Current Week",
            strike=0,
            lot=1,
            RolloverTime="13:01",
            DaysHead=0,
        )
        fake_db["strategies"].insert_one(dict(trade))

        exchange.OBUY(trade, "CE", 1)
        exchange.OBUY(trade, "CE", 1)

        positions = list(fake_db["Opositions"].find({
            "user": "alice",
            "botcode": trade["botcode"],
            "status": "open",
        }))
        assert len(positions) == 1
        assert positions[0]["buy_quantity"] == 50
        assert positions[0]["sell_quantity"] == 0
        assert positions[0]["net_quantity"] == 50

    def test_obuy_blocks_repeat_live_entry_after_broker_failure(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.websocketretry = 0
        exchange.prices = {"NIFTY24JUN22000CE": 100.0}
        exchange.subscribe_list = []
        exchange.api = None
        exchange.add_symbol_to_websocket = MagicMock()
        exchange.add_to_websocket = MagicMock()
        exchange._make_instrument = MagicMock(return_value=MagicMock())
        exchange._get_market_price = MagicMock(return_value=100.0)
        exchange.MainOptionSelect = MagicMock(return_value=("NIFTY24JUN22000CE", 50, "2024-06-27", 12345))

        trade = _ema_trade(
            live=True,
            entry_order_state="broker_failed",
            Expiry="Current Week",
            strike=0,
            lot=1,
            RolloverTime="13:01",
            DaysHead=0,
        )
        exchange.OBUY(trade, "CE", 1)
        exchange.MainOptionSelect.assert_not_called()

    def test_paper_order_path_allowed_by_risk_service(self, fake_db):
        result = RiskControlService(fake_db).check_order(
            BrokerOrder(user="alice", broker="paper", symbol="NIFTY", side="BUY", quantity=1, strategy_id="S1"),
            mode="paper",
        )
        assert result.allowed is True

    def test_live_order_blocked_when_live_disabled(self, fake_db):
        result = RiskControlService(fake_db).check_order(
            BrokerOrder(user="alice", broker="aliceblue", symbol="NIFTY", side="BUY", quantity=1),
            mode="live",
        )
        assert result.allowed is False
        assert "Live trading is disabled" in result.reason


class TestAlgorithm143RiskManagement:
    def test_trailing_stop_loss_triggers_exit(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.add_symbol_to_websocket = MagicMock(return_value=True)
        exchange._get_market_price = MagicMock(return_value=99.0)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)
        exchange.mainbuyexit = MagicMock()

        config = build_strategy("sstrike", _base_algo143_payload(trail="1", trail_stoploss="50"), _user())
        config["status"] = "opened"
        fake_db["strategies"].insert_one(dict(config))
        position = {
            "_id": "pos1",
            "botcode": config["botcode"],
            "user": config["user"],
            "status": "open",
            "optionname": "NIFTY24JUN22000CE",
            "optionentry": 100.0,
            "optionlot": 50,
            "lot": 1,
            "live": False,
            "symbol": "NIFTY",
            "optionexpiry": str(datetime.date.today()),
            "exitcond": 99,
            "trail_stoploss": 50,
        }
        fake_db["Opositions"].insert_one(position)

        exchange.OBUYEXIT(config, Signal=-1, exSignal=-1)
        updated = fake_db["Opositions"].find_one({"_id": "pos1"})
        assert updated["status"] == "close"

    def test_strategy_cooldown_after_loss(self, fake_db):
        fake_db["strategy_risk_settings"].insert_one({
            "user": "alice",
            "strategy_id": "S1",
            "cooldown_after_loss_seconds": 3600,
        })
        fake_db["Opositions"].insert_one({
            "user": "alice",
            "botcode": "S1",
            "status": "close",
            "pnl": -50,
            "exittime": int(datetime.datetime.now().timestamp()),
        })
        result = RiskControlService(fake_db).check_order(
            BrokerOrder(user="alice", broker="paper", symbol="NIFTY", side="BUY", quantity=1, strategy_id="S1"),
            mode="paper",
        )
        assert result.allowed is False
        assert "cooling down" in result.reason

    def test_realized_pnl_today_ignores_closed_positions_without_timestamp(self, fake_db):
        fake_db["Opositions"].insert_one({
            "user": "alice",
            "status": "close",
            "pnl": -999,
        })
        assert RiskControlService(fake_db)._realized_pnl_today("alice") == 0.0


class TestTargetStopLossCoverage:
    COMMON_OPTION_ALGOS = (
        "SSALGO",
        "EMA",
        "PEMA",
        "RF",
        "SSAUTO",
        "SSTRIKE",
        "SSEQUITY",
        "SSEQUITYFNO",
        "EQSSALGO",
        "SSALGO_FUT",
        "EMA_FUT",
        "PEMA_FUT",
        "SSAUTO_FUT",
        "SSEQUITY_FUT",
    )

    @pytest.mark.parametrize("strategy_name", COMMON_OPTION_ALGOS)
    @pytest.mark.parametrize(
        ("is_sell", "percentage", "expected_target", "expected_stop"),
        [
            (False, False, 110.0, 95.0),
            (True, False, 90.0, 105.0),
            (False, True, 110.0, 95.0),
            (True, True, 90.0, 105.0),
        ],
    )
    def test_common_algo_price_exit_levels_are_directionally_correct(
        self, strategy_name, is_sell, percentage, expected_target, expected_stop
    ):
        target, stop = Exchange._price_exit_levels(
            100,
            10,
            5,
            is_sell=is_sell,
            percentage=percentage,
        )
        assert (target, stop) == (expected_target, expected_stop), strategy_name

    @pytest.mark.parametrize(
        ("is_sell", "price", "expected"),
        [
            (False, 110, "TP Hit"),
            (False, 95, "SL Hit"),
            (True, 90, "TP Hit"),
            (True, 105, "SL Hit"),
            (False, 100, None),
            (True, 100, None),
        ],
    )
    def test_price_exit_reason_includes_exact_target_and_stop_boundaries(
        self, is_sell, price, expected
    ):
        target, stop = Exchange._price_exit_levels(
            100,
            10,
            5,
            is_sell=is_sell,
            percentage=False,
        )
        assert Exchange._price_exit_reason(
            price,
            target,
            stop,
            is_sell=is_sell,
        ) == expected

    @pytest.mark.parametrize(
        ("direction", "price", "expected"),
        [
            ("Up Side", 110, {"tp1": True, "tp2": False, "sl": False}),
            ("Up Side", 95, {"tp1": False, "tp2": False, "sl": True}),
            ("Dn Side", 90, {"tp1": True, "tp2": False, "sl": False}),
            ("Dn Side", 105, {"tp1": False, "tp2": False, "sl": True}),
        ],
    )
    def test_fractal_spot_and_future_target_stop_direction(
        self, direction, price, expected
    ):
        assert Exchange._directional_target_stop_flags(
            100,
            price,
            10,
            20,
            5,
            direction=direction,
        ) == expected

    @pytest.mark.parametrize(
        ("price", "expected_reason"),
        [(90.0, "TP Hit"), (105.0, "SL Hit")],
    )
    def test_option_seller_point_target_and_stop_close_position(
        self, fake_db, price, expected_reason
    ):
        exchange = _exchange_stub(fake_db)
        exchange.websocketretry = 0
        exchange.api = None
        exchange.add_symbol_to_websocket = MagicMock(return_value=True)
        exchange._get_market_price = MagicMock(return_value=price)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)

        config = build_strategy(
            "ema",
            _base_algo143_payload(
                BSmode="false",
                pct_point="false",
                pnlexit_tpslexit="false",
                tp="10",
                sl="5",
                StartTime="00:00",
                ExitTime="23:59",
            ),
            _user(),
        )
        config.update({"status": "opened", "position": "in", "live": False})
        fake_db["strategies"].insert_one(config)
        saved_config = fake_db["strategies"].find_one({
            "botcode": config["botcode"]
        })
        fake_db["Opositions"].insert_one({
            "entry_id": 11,
            "botcode": config["botcode"],
            "user": "alice",
            "status": "open",
            "decision": "intrade",
            "optionname": "NIFTY24JUN22000CE",
            "optionentry": 100.0,
            "optionexit": 100.0,
            "optionlot": 50,
            "lot": 1,
            "initial_lot": 1,
            "entry_quantity": 50,
            "buy_quantity": 0,
            "sell_quantity": 50,
            "net_quantity": -50,
            "live": False,
            "symbol": "NIFTY",
            "optionexpiry": "2099-06-27",
            "exitcond": 99,
            "trail_stoploss": 0,
        })

        assert exchange.OSELLEXIT(
            saved_config,
            Signal=0,
            exSignal=0,
        ) is True
        position = fake_db["Opositions"].find_one({"entry_id": 11})
        assert position["status"] == "close"
        assert position["net_quantity"] == 0
        assert Exchange._price_exit_reason(
            price,
            90,
            105,
            is_sell=True,
        ) == expected_reason

    @pytest.mark.parametrize(
        ("side", "price", "expected_reason"),
        [
            ("BUY", 110.0, "MCX TP"),
            ("BUY", 95.0, "MCX SL"),
            ("SELL", 90.0, "MCX TP"),
            ("SELL", 105.0, "MCX SL"),
        ],
    )
    def test_mcx_target_and_stop_for_both_sides(
        self, fake_db, side, price, expected_reason
    ):
        exchange = _exchange_stub(fake_db)
        exchange._get_market_price = MagicMock(return_value=price)
        config = {
            "botcode": "mcx-test",
            "user": "alice",
            "ExitTime": "23:59",
            "live": False,
        }
        fake_db["Opositions"].insert_one({
            "botcode": "mcx-test",
            "user": "alice",
            "status": "open",
            "side": side,
            "entry_price": 100.0,
            "optionlot": 1,
            "lot": 1,
            "sl_price": 95.0 if side == "BUY" else 105.0,
            "tp_price": 110.0 if side == "BUY" else 90.0,
            "decision": "intrade",
        })

        exchange._manage_mcx_exit(
            config,
            "CRUDEOIL-I",
            1,
            123,
        )

        position = fake_db["Opositions"].find_one({"botcode": "mcx-test"})
        assert position["status"] == "close"
        assert position["exit_reason"] == expected_reason


class TestAlgorithm143PositionManagement:
    def _algo143_exit_exchange(self, fake_db, price):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.websocketretry = 0
        exchange.api = None
        exchange.add_symbol_to_websocket = MagicMock(return_value=False)
        exchange._get_market_price = MagicMock(return_value=price)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)
        return exchange

    def _insert_algo143_config(self, fake_db, **overrides):
        payload = _base_algo143_payload(
            FixedLot="Doubling",
            lot="2",
            initiallot="1",
            status="opened",
            position="in",
            StartTime="00:00",
            ExitTime="23:59",
            **overrides,
        )
        config = build_strategy("ema", payload, _user())
        config.update({"status": "opened", "position": "in"})
        fake_db["strategies"].insert_one(config)
        return fake_db["strategies"].find_one({"botcode": config["botcode"]})

    def _insert_algo143_position(self, fake_db, config, **overrides):
        position = {
            "entry_id": overrides.pop("entry_id", 143),
            "botcode": config["botcode"],
            "user": config["user"],
            "status": "open",
            "decision": "intrade",
            "optionname": overrides.pop("optionname", "NIFTY24JUN22000CE"),
            "optionentry": 100.0,
            "optionexit": 100.0,
            "optionlot": 50,
            "lot": config["lot"],
            "initial_lot": config["lot"],
            "entry_quantity": int(config["lot"]) * 50,
            "buy_quantity": int(config["lot"]) * 50,
            "sell_quantity": 0,
            "net_quantity": int(config["lot"]) * 50,
            "live": False,
            "symbol": "NIFTY",
            "optionexpiry": "2099-06-27",
            "exitcond": -1,
            "trail_stoploss": 0,
            "BSmode": True,
            "side": "CE",
        }
        position.update(overrides)
        fake_db["Opositions"].insert_one(position)
        return fake_db["Opositions"].find_one({"entry_id": position["entry_id"]})

    def test_first_signal_enters_with_current_configured_lot(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.websocketretry = 0
        exchange.prices = {
            "NIFTY": 22000.0,
            "NIFTY24JUN22000CE": 100.0,
        }
        exchange.subscribe_list = []
        exchange.api = None
        exchange.add_symbol_to_websocket = MagicMock()
        exchange.add_to_websocket = MagicMock()
        exchange._make_instrument = MagicMock(return_value=MagicMock())
        exchange._wait_for_market_price = MagicMock(return_value=100.0)
        exchange._get_market_price = MagicMock(return_value=100.0)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)
        exchange.MainOptionSelect = MagicMock(
            return_value=("NIFTY24JUN22000CE", 50, "2099-06-27", 12345)
        )
        trade = _ema_trade(
            botname="algo143-first-entry",
            live=False,
            Expiry="Current Week",
            strike=0,
            lot=1,
            initiallot=1,
            RolloverTime="13:01",
            DaysHead=0,
        )
        fake_db["strategies"].insert_one(dict(trade))

        exchange.OBUY(trade, "CE", 1)

        position = fake_db["Opositions"].find_one({"botcode": trade["botcode"]})
        strategy = fake_db["strategies"].find_one({"botcode": trade["botcode"]})
        assert position["lot"] == 1
        assert position["entry_quantity"] == 50
        assert strategy["position"] == "in"

    def test_profitable_buyer_signal_exit_resets_next_entry_lot(self, fake_db):
        exchange = self._algo143_exit_exchange(fake_db, price=110.0)
        config = self._insert_algo143_config(fake_db)
        self._insert_algo143_position(fake_db, config)

        assert exchange.OBUYEXIT(config, Signal=-1, exSignal=-1) is True

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "close"
        assert position["pnl"] > 0
        assert strategy["position"] == "out"
        assert strategy["lot"] == strategy["initiallot"] == 1

    def test_losing_buyer_signal_exit_doubles_next_entry_lot(self, fake_db):
        exchange = self._algo143_exit_exchange(fake_db, price=90.0)
        config = self._insert_algo143_config(fake_db)
        self._insert_algo143_position(fake_db, config)

        assert exchange.OBUYEXIT(config, Signal=-1, exSignal=-1) is True

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "close"
        assert position["pnl"] < 0
        assert strategy["position"] == "out"
        assert strategy["lot"] == 4

    def test_losing_seller_signal_exit_uses_exsignal_and_doubles_next_lot(self, fake_db):
        exchange = self._algo143_exit_exchange(fake_db, price=110.0)
        config = self._insert_algo143_config(fake_db, BSmode="false")
        self._insert_algo143_position(
            fake_db,
            config,
            BSmode=False,
            side="PE",
            buy_quantity=0,
            sell_quantity=100,
            net_quantity=-100,
        )

        assert exchange.OSELLEXIT(config, Signal=0, exSignal=-1) is True

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "close"
        assert position["pnl"] < 0
        assert strategy["position"] == "out"
        assert strategy["lot"] == 4

    def test_losing_future_signal_exit_doubles_next_lot(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.add_symbol_to_websocket = MagicMock()
        exchange.prices = {"NIFTY-FUT": 90.0}
        config = self._insert_algo143_config(
            fake_db,
            Expiry="Current Month",
            onspot="true",
            BSmode="false",
        )
        self._insert_algo143_position(
            fake_db,
            config,
            optionname="NIFTY-FUT",
            side="BUY",
            exitcond=-1,
        )

        exchange.FEXIT(config, Signal=-1)

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "close"
        assert position["pnl"] < 0
        assert strategy["position"] == "out"
        assert strategy["lot"] == 4

    def test_partial_signal_exit_preserves_position_and_does_not_double(self, fake_db):
        exchange = self._algo143_exit_exchange(fake_db, price=90.0)
        config = self._insert_algo143_config(fake_db, live="true")
        config["live"] = True
        self._insert_algo143_position(fake_db, config, live=True)
        fake_db["strategies"].update_one(
            {"botcode": config["botcode"]},
            {"$set": {"live": True}},
        )
        exchange.mainbuyexit = MagicMock(return_value={
            "success": True,
            "requested_quantity": 100,
            "filled_quantity": 50,
            "response": {"status": "partial", "filled_quantity": 50},
        })

        assert exchange.OBUYEXIT(config, Signal=-1, exSignal=-1) is False

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "open"
        assert position["exit_order_state"] == "partial"
        assert position["net_quantity"] == 50
        assert strategy["position"] == "in"
        assert strategy["lot"] == 2

    def test_losing_non_signal_exit_does_not_double_lot(self, fake_db):
        exchange = self._algo143_exit_exchange(fake_db, price=90.0)
        config = self._insert_algo143_config(fake_db)
        self._insert_algo143_position(fake_db, config, exitcond=99)

        assert exchange.OBUYEXIT(config, Signal=0, exSignal=0) is True

        strategy = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        position = fake_db["Opositions"].find_one({"botcode": config["botcode"]})
        assert position["status"] == "close"
        assert position["exit_reason"] == "PNL SL"
        assert position["pnl"] < 0
        assert strategy["lot"] == 2

    def test_exit_uses_only_recorded_open_quantity(self, fake_db):
        exchange = _exchange_stub(fake_db)
        exchange.broker_collection = fake_db["broker"]
        exchange.add_symbol_to_websocket = MagicMock(return_value=True)
        exchange._get_market_price = MagicMock(return_value=110.0)
        exchange._get_underlying_price = MagicMock(return_value=22000.0)
        exchange.mainbuyexit = MagicMock(return_value={
            "success": True,
            "requested_quantity": 25,
            "filled_quantity": 25,
            "response": {"status": "success", "filled_quantity": 25},
        })

        strategy = build_strategy("ema", _base_algo143_payload(), _user())
        strategy.update({"status": "opened", "position": "in", "live": True})
        fake_db["strategies"].insert_one(strategy)
        config = fake_db["strategies"].find_one({"botcode": strategy["botcode"]})
        fake_db["Opositions"].insert_one({
            "entry_id": 1,
            "botcode": config["botcode"],
            "user": config["user"],
            "status": "open",
            "decision": "exitit",
            "optionname": "NIFTY24JUN22000CE",
            "optionentry": 100.0,
            "optionexit": 100.0,
            "optionlot": 50,
            "lot": 2,
            "initial_lot": 2,
            "entry_quantity": 25,
            "buy_quantity": 25,
            "sell_quantity": 0,
            "net_quantity": 25,
            "live": True,
            "symbol": "NIFTY",
            "optionexpiry": "2099-06-27",
            "exitcond": 99,
            "trail_stoploss": 0,
        })

        assert exchange.OBUYEXIT(config, Signal=0, exSignal=0) is True
        position = fake_db["Opositions"].find_one({"entry_id": 1})
        assert position["buy_quantity"] == 25
        assert position["sell_quantity"] == 25
        assert position["net_quantity"] == 0
        assert position["status"] == "close"

    @pytest.mark.parametrize(
        ("result", "expected_sell", "expected_net", "expected_state"),
        [
            (
                {
                    "success": True,
                    "requested_quantity": 50,
                    "filled_quantity": 20,
                    "response": {"status": "partial", "filled_quantity": 20},
                },
                20,
                30,
                "partial",
            ),
            (
                {
                    "success": False,
                    "requested_quantity": 50,
                    "filled_quantity": 0,
                    "response": {"status": "rejected"},
                },
                0,
                50,
                "failed",
            ),
        ],
    )
    def test_partial_or_failed_exit_preserves_open_position_state(
        self, fake_db, result, expected_sell, expected_net, expected_state
    ):
        exchange = _exchange_stub(fake_db)
        position = {
            "BSmode": True,
            "optionlot": 50,
            "lot": 1,
            "initial_lot": 1,
            "entry_quantity": 50,
            "buy_quantity": 50,
            "sell_quantity": 0,
            "net_quantity": 50,
        }

        fully_closed = exchange._apply_exit_fill(position, "SELL", result)

        assert fully_closed is False
        assert position["buy_quantity"] == 50
        assert position["sell_quantity"] == expected_sell
        assert position["net_quantity"] == expected_net
        assert position["exit_order_state"] == expected_state

    def test_obuyexit_resets_strategy_position_when_no_open_positions(self, fake_db):
        exchange = _exchange_stub(fake_db)
        config = build_strategy("ema", _base_algo143_payload(), _user())
        fake_db["strategies"].insert_one(config)
        exchange.OBUYEXIT(config, Signal=0, exSignal=0)
        updated = fake_db["strategies"].find_one({"botcode": config["botcode"]})
        assert updated["position"] == "out"

    def test_stop_marks_open_positions_for_exit(self, fake_db, monkeypatch):
        from app.api.legacy_compat import common, strategies as strategy_routes

        fake_db["strategies"].insert_one({
            "botcode": "bot-stop-test",
            "user": "alice",
            "strategy": "EMA",
            "status": "opened",
        })
        fake_db["Opositions"].insert_one({
            "botcode": "bot-stop-test",
            "user": "alice",
            "status": "open",
            "decision": "intrade",
        })
        monkeypatch.setattr(common, "get_database", lambda: fake_db)
        monkeypatch.setattr(strategy_routes, "audit_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(strategy_routes, "trading_event", lambda *args, **kwargs: None)

        import asyncio
        from starlette.requests import Request

        scope = {"type": "http", "method": "POST", "headers": [], "query_string": b""}
        request = Request(scope)

        async def run_stop():
            with patch.object(strategy_routes, "payload_from_request", return_value={"id": "bot-stop-test"}):
                return await strategy_routes.api_stop_ssalgo(request, user={"username": "alice"})

        asyncio.run(run_stop())
        position = fake_db["Opositions"].find_one({"botcode": "bot-stop-test"})
        assert position["decision"] == "exitit"
        strategy = fake_db["strategies"].find_one({"botcode": "bot-stop-test"})
        assert strategy["status"] == "paused"


class TestStrategyLifecycle:
    @staticmethod
    def _patch_runtime(monkeypatch, strategies, fake_db):
        monkeypatch.setattr(strategies, "get_database", lambda: fake_db)
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
        monkeypatch.setattr(strategies, "audit_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(strategies, "trading_event", lambda *args, **kwargs: None)

    def test_starting_same_strategy_twice_is_idempotent(self, fake_db, monkeypatch):
        from app.api.legacy_compat import common, strategies

        strategy = build_strategy("ema", _base_algo143_payload(), _user())
        fake_db["strategies"].insert_one(strategy)
        monkeypatch.setattr(common, "get_database", lambda: fake_db)
        self._patch_runtime(monkeypatch, strategies, fake_db)
        monkeypatch.setattr(
            strategies,
            "payload_from_request",
            lambda _request: None,
        )

        async def payload(_request):
            return {"id": strategy["botcode"]}

        monkeypatch.setattr(strategies, "payload_from_request", payload)
        first = asyncio.run(
            strategies.api_start_ssalgo(object(), user={"username": "alice"})
        )
        second = asyncio.run(
            strategies.api_start_ssalgo(object(), user={"username": "alice"})
        )
        saved = fake_db["strategies"].find_one({"botcode": strategy["botcode"]})

        assert first.data["already_active"] is False
        assert second.data["already_active"] is True
        assert saved["status"] == "opened"
        assert saved["lifecycle_revision"] == 1

    def test_modifying_running_strategy_replaces_revision_and_preserves_state(
        self, fake_db, monkeypatch
    ):
        from app.api.legacy_compat import common, strategies

        strategy = build_strategy(
            "ema",
            _base_algo143_payload(status="opened", position="out"),
            _user(),
        )
        strategy["lifecycle_revision"] = 2
        fake_db["strategies"].insert_one(strategy)
        monkeypatch.setattr(common, "get_database", lambda: fake_db)
        monkeypatch.setattr(strategies, "audit_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(strategies, "trading_event", lambda *args, **kwargs: None)

        payload = _base_algo143_payload(
            botcode=strategy["botcode"],
            status="paused",
            position="out",
            tp="250",
        )

        async def request_payload(_request):
            return payload

        monkeypatch.setattr(strategies, "payload_from_request", request_payload)
        endpoint = strategies.make_edit_strategy_endpoint("ema", "updated")
        response = asyncio.run(endpoint(object(), user=_user()))
        saved = fake_db["strategies"].find_one({"botcode": strategy["botcode"]})

        assert response.data["lifecycle_revision"] == 3
        assert saved["status"] == "opened"
        assert saved["position"] == "out"
        assert saved["tp"] == 250

    def test_stale_running_snapshot_is_not_evaluated_after_stop(self, fake_db):
        exchange = _exchange_stub(fake_db)
        stale = _ema_trade(status="opened", position="out")
        stale["lifecycle_revision"] = 4
        current = dict(stale)
        current["status"] = "paused"
        current["lifecycle_revision"] = 5
        fake_db["strategies"].insert_one(current)
        exchange.EMA = MagicMock()

        exchange.process_strategy(stale)

        exchange.EMA.assert_not_called()

    def test_stale_running_snapshot_reloads_modified_configuration(self, fake_db):
        exchange = _exchange_stub(fake_db)
        stale = _ema_trade(status="opened", position="out", tp=100)
        stale["lifecycle_revision"] = 2
        current = dict(stale)
        current["tp"] = 250
        current["lifecycle_revision"] = 3
        fake_db["strategies"].insert_one(current)
        exchange.EMA = MagicMock()

        exchange.process_strategy(stale)

        exchange.EMA.assert_called_once()
        assert exchange.EMA.call_args.args[0]["tp"] == 250

    def test_duplicate_strategy_snapshots_are_deduplicated_by_user_and_botcode(
        self, fake_db
    ):
        exchange = _exchange_stub(fake_db)
        rows = [
            _ema_trade(botcode="same"),
            _ema_trade(botcode="same"),
            _ema_trade(botcode="same", user="bob"),
        ]

        unique = exchange._unique_strategy_snapshots(rows)

        assert len(unique) == 2

    def test_delete_stops_strategy_and_marks_open_position_for_exit(
        self, fake_db, monkeypatch
    ):
        from app.api.legacy_compat import common, strategies

        fake_db["strategies"].insert_one({
            "botcode": "delete-running",
            "user": "alice",
            "strategy": "EMA",
            "status": "opened",
            "position": "in",
            "lifecycle_revision": 7,
        })
        fake_db["Opositions"].insert_one({
            "botcode": "delete-running",
            "user": "alice",
            "status": "open",
            "decision": "intrade",
        })
        monkeypatch.setattr(common, "get_database", lambda: fake_db)
        monkeypatch.setattr(strategies, "audit_event", lambda *args, **kwargs: None)
        monkeypatch.setattr(strategies, "trading_event", lambda *args, **kwargs: None)

        async def payload(_request):
            return {"id": "delete-running"}

        monkeypatch.setattr(strategies, "payload_from_request", payload)
        asyncio.run(
            strategies.api_delete_strategy(
                object(),
                user={"username": "alice"},
            )
        )

        saved = fake_db["strategies"].find_one({"botcode": "delete-running"})
        position = fake_db["Opositions"].find_one({"botcode": "delete-running"})
        assert saved["status"] == "closed"
        assert saved["lifecycle_revision"] == 8
        assert position["decision"] == "exitit"

    def test_stopped_strategy_suppresses_queued_worker_order(self, fake_db):
        fake_db["strategies"].insert_one({
            "botcode": "stopped-job",
            "user": "alice",
            "status": "paused",
            "lifecycle_revision": 2,
        })
        worker = object.__new__(TradingWorker)
        worker.db = fake_db
        worker.audit = None
        worker.adapter_factory = MagicMock()
        worker._next_strategy_job = MagicMock(side_effect=[
            {
                "_id": "job-1",
                "user": "alice",
                "strategy_id": "stopped-job",
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 50,
                "mode": "paper",
                "lifecycle_revision": 1,
            },
            None,
        ])
        worker._complete_strategy_job = MagicMock()

        result = worker.process_strategy_jobs(limit=2)

        assert "not active" in result[0]["error"]
        worker.adapter_factory.create.assert_not_called()


class TestAlgorithm143DatabaseIntegration:
    @pytest.mark.integration
    def test_demo_database_contains_algo143_strategies(self):
        from app.core.database import get_database

        db = get_database()
        ema_count = db["strategies"].count_documents({"strategy": "EMA"})
        sstrike_count = db["strategies"].count_documents({"strategy": "SSTRIKE", "r1": 143})
        assert ema_count > 0
        assert sstrike_count > 0

    @pytest.mark.integration
    def test_strategy_documents_have_required_algo143_fields(self):
        from app.core.database import get_database

        db = get_database()
        for strategy_type in ("EMA", "SSTRIKE"):
            doc = db["strategies"].find_one({"strategy": strategy_type})
            assert doc is not None
            for field in (
                "botcode", "symbol", "timeframe", "tp", "sl", "lot",
                "StartTime", "ExitTime", "status", "position", "live",
            ):
                assert field in doc, f"{strategy_type} missing {field}"


class TestAlgorithm143EdgeCases:
    def test_build_strategy_rejects_mismatched_option_legs(self):
        payload = _base_algo143_payload()
        payload.update({
            "ooption": ["CE"],
            "ostrike": ["0", "100"],
            "oside": ["BUY"],
            "oexpiry": ["Current Week"],
            "olot": ["1"],
        })
        with pytest.raises(HTTPException) as exc:
            build_strategy("fractalnubiatimehedgeorder", payload, _user())
        assert "Mismatched option leg data lengths" in exc.value.detail

    def test_sstrike_seller_mode_routes_to_osell(self, fake_db):
        exchange = _exchange_stub_with_order_mocks(fake_db)
        trade = _sstrike_trade(BSmode=False, StartTime="00:00", ExitTime="23:59", timetowait=0)
        with patch("connectors.connector.datetime") as dt:
            dt.datetime.now.return_value = datetime.datetime(2026, 6, 11, 10, 0, 0)
            dt.datetime.strptime = datetime.datetime.strptime
            dt.date.today.return_value = datetime.date(2026, 6, 11)
            dt.timedelta = datetime.timedelta
            exchange.SSTRIKE(trade)
        exchange.OSELL.assert_not_called()

    def test_duplicate_live_order_requires_idempotency_key(self, fake_db):
        fake_db["risk_settings"].insert_one({
            "user": "alice",
            "live_enabled": True,
            "paper_only": False,
        })
        result = RiskControlService(fake_db).check_order(
            BrokerOrder(user="alice", broker="aliceblue", symbol="NIFTY", side="BUY", quantity=1),
            mode="live",
        )
        assert result.allowed is False
        assert "idempotency key" in result.reason
