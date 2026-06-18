import os
import time
import datetime

from app.api.legacy_compat.common import *
from app.core.config import AppConfig
from app.core.trading_debug import trading_event
from app.domain.brokers.health import BrokerHealthService
from app.domain.market_data import MarketFeedManager, MarketPriceRepository
from app.workers.control import WorkerControlService


def _strategy_symbols(strategy):
    symbols = strategy.get("symbol") or strategy.get("symbol[]") or []
    if isinstance(symbols, str):
        symbols = [symbols]
    return sorted({str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()})


def _strategy_price_required_now(strategy, now=None):
    """Require a fresh tick only while the strategy can currently execute."""
    india_timezone = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    if now is None:
        now = datetime.datetime.now(datetime.UTC).astimezone(india_timezone)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=india_timezone)
    else:
        now = now.astimezone(india_timezone)
    if now.weekday() >= 5:
        return False
    try:
        start_time = datetime.datetime.strptime(
            str(strategy.get("StartTime") or ""),
            "%H:%M",
        ).time()
        exit_time = datetime.datetime.strptime(
            str(strategy.get("ExitTime") or ""),
            "%H:%M",
        ).time()
    except (TypeError, ValueError):
        return True
    current_time = now.time().replace(tzinfo=None)
    return start_time < current_time < exit_time


def api_strategys(_admin=Depends(require_admin)):
    data = [
        clean_document(doc)
        for doc in collection("strategies").find({})
        if doc.get("status") in ACTIVE_STRATEGY_STATUSES
    ]
    return response("Fetched Successfully Strategies", data)


async def api_add_strategy_form(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy").lower()
    if not strategy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="strategy is required")
    page = f"{strategy}.html"
    action_url = "/" + "api_" + strategy.replace("_form", "")
    strategy_limit = int(user.get("StrategyLimit", 10))
    return response("Successfully Fetched Strategy Form", {
        "page": strategy_forms().get(page, []),
        "StrategyLimit": strategy_limit,
        "StrategyRemaining": strategy_limit - active_strategy_units(current_username(user)),
        "action_url": action_url,
    })


async def api_edit_strategy_form(order_time: str, user=Depends(get_current_user)):
    username = current_username(user)
    order = collection("strategies").find_one({"botcode": str(order_time), "user": username})
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    info = clean_document(order) or {}
    info.pop("_id", None)
    if info.get("strategy") == "EQSSALGO":
        info.pop("symbol", None)
    readonly = ["botname", "_id", "symbol", "time", "Expiry", "BSmode", "lot", "initiallot", "MultiFactor", "candle1", "candle2"]
    if info.get("status") == "paused":
        readonly = ["botname", "_id", "symbol", "time", "Expiry", "BSmode", "MultiFactor", "candle1", "candle2"]
    limit = int(user.get("StrategyLimit", 10))
    algo = str(order.get("strategy", "")).lower()
    return response("Successfully Fetched Strategy Form", {
        "page": strategy_forms().get(strategy_form_page(order), []),
        "StrategyLimit": limit,
        "StrategyRemaining": limit - active_strategy_units(username),
        "info": info,
        "action_url": f"/api_edit_{algo}",
        "readonly": readonly,
    })


def api_edit_admin_strategy_form(order_time: str, _admin=Depends(require_admin)):
    order = collection("strategies").find_one({"botcode": str(order_time)})
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    info = clean_document(order) or {}
    info.pop("_id", None)
    algo = str(order.get("strategy", "")).lower()
    return response("Successfully Fetched Strategy Form", {
        "page": strategy_forms().get(strategy_form_page(order), []),
        "info": info,
        "action_url": f"/api_edit_admin_{algo}",
    })


async def api_edit_strategyinput(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    if not strategy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="strategy is required")
    existing = collection("strategyinput").find_one({"strategy": strategy})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    data = {
        "strategy": strategy,
        "r1": float(form_value(payload, "r1")),
        "k1": float(form_value(payload, "k1")),
        "r2": float(form_value(payload, "r2")),
        "k2": float(form_value(payload, "k2")),
        "timeframe": form_value(payload, "timeframe"),
    }
    collection("strategyinput").update_one({"strategy": strategy}, {"$set": data})
    return response("Strategy input updated successfully")


async def api_edit_strategyinput_form(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    order = collection("strategyinput").find_one({"strategy": strategy})
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    data = clean_document(order) or {}
    data["action_url"] = "/api_edit_strategyinput"
    return response("Strategy input form fetched", data)


def make_add_strategy_endpoint(kind, message):
    async def endpoint(request: Request, user=Depends(get_current_user)):
        payload = await payload_from_request(request)
        doc = build_strategy(kind, payload, user)
        inserted_id = collection("strategies").insert_one(doc).inserted_id
        audit_event("strategy_created", user=current_username(user), resource_type="strategy", resource_id=doc.get("botcode"), details={"kind": kind})
        return response(message, {"id": str(inserted_id), "botcode": doc.get("botcode")})

    endpoint.__name__ = f"add_{kind}_strategy"
    return endpoint


def make_edit_strategy_endpoint(kind, message, *, admin=False):
    async def endpoint(request: Request, user=Depends(require_admin if admin else get_current_user)):
        payload = await payload_from_request(request)
        botcode = form_value(payload, "botcode") or form_value(payload, "id")
        if not botcode:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="botcode is missing")
        query = {"botcode": botcode}
        if not admin:
            query["user"] = current_username(user)
        existing = collection("strategies").find_one(query)
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
        doc = build_strategy(kind, payload, user, existing=existing, admin=admin)
        update = {"$set": doc}
        if kind == "fractalnubiatimehedgeorder" and (
            existing.get("legs") != doc.get("legs") or existing.get("method") != doc.get("method")
        ):
            update = fractal_reset_update(botcode, None if admin else current_username(user), doc)
        result = collection("strategies").update_one(query, update)
        if result.matched_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
        audit_event(
            "strategy_updated",
            user=existing.get("user") or current_username(user),
            resource_type="strategy",
            resource_id=botcode,
            actor=current_username(user),
            details={"kind": kind, "admin": admin},
        )
        return response(message, {"botcode": botcode})

    endpoint.__name__ = f"{'admin_' if admin else ''}edit_{kind}_strategy"
    return endpoint


async def api_stop_ssalgo(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one(
        {"botcode": botcode, "user": current_username(user)},
        {"$set": {"status": "paused"}},
    )
    mark_strategy_positions_exit(botcode, current_username(user))
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    audit_event("strategy_paused", user=current_username(user), resource_type="strategy", resource_id=botcode)
    trading_event("strategy_stopped", user=current_username(user), strategy_id=botcode, force=True)
    return response("Successfully Stop SSALGO Strategy")


async def api_start_ssalgo(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    db = get_database()
    username = current_username(user)
    runtime = WorkerControlService(db).get_status()
    runtime_ready = runtime.get("healthy") is True and runtime.get("strategy_engine") == "running"
    if not runtime_ready:
        trading_event(
            "strategy_start_rejected",
            user=current_username(user),
            strategy_id=botcode,
            force=True,
            reason="trading_runtime_not_ready",
            runtime=runtime,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trading runtime is not running. Start the trading worker before starting strategies.",
        )
    strategy = db["strategies"].find_one(
        {"botcode": botcode, "user": username}
    )
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found",
        )

    if bool(strategy.get("live")):
        broker_health_service = BrokerHealthService(db)
        selected_broker = broker_health_service.active_broker(username)
        selected_health = broker_health_service.get_health(
            username, selected_broker
        )
        strategy_symbols = _strategy_symbols(strategy)
        price_repository = MarketPriceRepository(db)
        feed_health = price_repository.get_global_health()
        feed_provider = (
            feed_health.get("active_provider")
            or AppConfig.MARKET_FEED_PROVIDER
        )
        price_status = price_repository.has_fresh_prices(
            strategy_symbols,
            provider=None,
        )
        feed_result = {
            "success": price_status.get("ready", False),
            "provider": next(iter(price_status.get("providers", {}).values()), feed_provider),
            "status": "connected" if price_status.get("ready") else "pending",
            "message": "Using existing shared market prices" if price_status.get("ready") else "Market feed warmup required",
        }
        if not price_status.get("ready"):
            feed_result = MarketFeedManager(db).ensure_symbols(
                strategy_symbols,
                user=username,
                broker=selected_broker,
            )
            feed_health = price_repository.get_global_health()
            feed_provider = (
                feed_health.get("active_provider")
                or AppConfig.MARKET_FEED_PROVIDER
            )
        price_required_now = _strategy_price_required_now(strategy)
        if price_required_now and not price_status.get("ready") and feed_result.get("success"):
            deadline = time.monotonic() + float(os.getenv("SSLAGO_MARKET_FEED_WARMUP_SECONDS", "3"))
            while time.monotonic() < deadline:
                time.sleep(0.2)
                price_status = price_repository.has_fresh_prices(
                    strategy_symbols,
                    provider=None,
                )
                if price_status.get("ready"):
                    break
        live_blockers = []
        if selected_broker == "paper":
            live_blockers.append("Select a live broker")
        if selected_health.get("login_status") != "connected":
            live_blockers.append("Broker login is not connected")
        if feed_health.get("status") != "connected" and feed_health.get("connected") is not True:
            live_blockers.append("Market feed is not connected")
        if price_required_now and not price_status.get("ready"):
            missing = ", ".join(price_status.get("missing") or [])
            stale = ", ".join(price_status.get("stale") or [])
            if missing:
                live_blockers.append(f"Market feed price missing for {missing}")
            if stale:
                live_blockers.append(f"Market feed price stale for {stale}")
        elif not price_required_now and not price_status.get("ready"):
            trading_event(
                "market_feed_price_pending",
                user=username,
                strategy_id=botcode,
                provider=feed_provider,
                symbols=strategy_symbols,
                market_price_status=price_status,
                reason="outside_strategy_window",
            )
        if live_blockers:
            trading_event(
                "strategy_start_rejected",
                user=username,
                strategy_id=botcode,
                force=True,
                reason="live_broker_not_ready",
                broker=selected_broker,
                broker_health=selected_health,
                market_feed_result=feed_result,
                market_feed_health=feed_health,
                market_price_status=price_status,
                price_required_now=price_required_now,
                blockers=live_blockers,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Live broker is not ready",
                    "broker": selected_broker,
                    "blockers": live_blockers,
                },
            )

    start_update = fractal_reset_update(
        botcode,
        username,
        {"status": "opened"},
    )
    failed_entry_state = strategy.get("entry_order_state") in {
        "broker_failed",
        "preflight_failed",
    }
    if strategy.get("position") != "in" and failed_entry_state:
        start_update.setdefault("$set", {})["position"] = "out"
        start_update.setdefault("$unset", {}).update({
            "entry_order_state": "",
            "entry_order_time": "",
            "last_broker_order_error": "",
            "last_broker_order_error_time": "",
            "state_repair_reason": "",
            "state_repair_time": "",
        })
    result = collection("strategies").update_one(
        {"botcode": botcode, "user": username},
        start_update,
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    strategy = collection("strategies").find_one({"botcode": botcode, "user": username}) or {}
    details = {
        "strategy": strategy.get("strategy"),
        "symbol": strategy.get("symbol"),
        "timeframe": strategy.get("timeframe"),
        "live": strategy.get("live"),
        "position": strategy.get("position"),
        "runtime": runtime,
        "runtime_ready": runtime_ready,
    }
    audit_event(
        "strategy_started",
        user=current_username(user),
        resource_type="strategy",
        resource_id=botcode,
        details=details,
    )
    trading_event(
        "strategy_started",
        user=current_username(user),
        strategy_id=botcode,
        force=True,
        **details,
    )
    return response(
        "Successfully started SSALGO strategy",
        {"runtime": runtime, "runtime_ready": runtime_ready},
    )


async def api_stop_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one({"botcode": botcode}, {"$set": {"status": "paused"}})
    mark_strategy_positions_exit(botcode)
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    audit_event("strategy_admin_paused", resource_type="strategy", resource_id=botcode, actor=_admin.get("username"))
    return response("Successfully stopped SSALGO strategy")


async def api_start_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one(
        {"botcode": botcode},
        fractal_reset_update(botcode, None, {"status": "opened"}),
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    audit_event("strategy_admin_started", resource_type="strategy", resource_id=botcode, actor=_admin.get("username"))
    return response("Successfully started SSALGO strategy")


async def api_delete_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one({"botcode": botcode}, {"$set": {"status": "closed"}})
    mark_strategy_positions_exit(botcode)
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found.")
    audit_event("strategy_admin_closed", resource_type="strategy", resource_id=botcode, actor=_admin.get("username"))
    return response("Successfully closed the strategy.")


async def api_delete_strategy(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one(
        {"botcode": botcode, "user": current_username(user)},
        {"$set": {"status": "closed"}},
    )
    mark_strategy_positions_exit(botcode, current_username(user))
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found or you do not have permission to close it.")
    audit_event("strategy_closed", user=current_username(user), resource_type="strategy", resource_id=botcode)
    return response("Successfully closed the strategy.")


router = APIRouter(tags=["legacy strategies"])

router.add_api_route("/api_strategys", api_strategys, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_add_strategy_form", api_add_strategy_form, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_add_strategy_form/", api_add_strategy_form, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_edit_strategy_form/{order_time}", api_edit_strategy_form, methods=["POST"], response_model=ApiResponse)
router.add_api_route(
    "/api_edit_admin_strategy_form/{order_time}",
    api_edit_admin_strategy_form,
    methods=["POST"],
    response_model=ApiResponse,
)
router.add_api_route("/api_edit_strategyinput", api_edit_strategyinput, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_edit_strategyinput_form", api_edit_strategyinput_form, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_stop_ssalgo", api_stop_ssalgo, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_ssalgo", api_start_ssalgo, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_stop_admin_ssalgo", api_stop_admin_ssalgo, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_admin_ssalgo", api_start_admin_ssalgo, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_admin_ssalgo", api_delete_admin_ssalgo, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_strategy", api_delete_strategy, methods=["POST"], response_model=ApiResponse)

for route_path, (strategy_kind, success_message) in ADD_STRATEGY_ROUTES.items():
    router.add_api_route(
        route_path,
        make_add_strategy_endpoint(strategy_kind, success_message),
        methods=["POST"],
        response_model=ApiResponse,
    )

for route_path, (strategy_kind, success_message) in EDIT_STRATEGY_ROUTES.items():
    router.add_api_route(
        route_path,
        make_edit_strategy_endpoint(strategy_kind, success_message),
        methods=["POST"],
        response_model=ApiResponse,
    )

for route_path, (strategy_kind, success_message) in ADMIN_EDIT_STRATEGY_ROUTES.items():
    router.add_api_route(
        route_path,
        make_edit_strategy_endpoint(strategy_kind, success_message, admin=True),
        methods=["POST"],
        response_model=ApiResponse,
    )
    router.add_api_route(
        f"{route_path}/",
        make_edit_strategy_endpoint(strategy_kind, success_message, admin=True),
        methods=["POST"],
        response_model=ApiResponse,
    )
