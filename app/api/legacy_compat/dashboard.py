import logging
import time

from app.api.legacy_compat.common import *
from app.domain.brokers.health import BrokerHealthService
from app.workers.control import WorkerControlService
from connectors.contracts import contract_file_path


logger = logging.getLogger(__name__)
DASHBOARD_HISTORY_LIMIT = 100


def _elapsed_ms(start):
    return round((time.perf_counter() - start) * 1000, 2)


def _latest_first(cursor):
    return cursor.sort("$natural", -1).limit(DASHBOARD_HISTORY_LIMIT)


def _position_with_quantities(doc):
    position = clean_document(doc)
    if not position:
        return position
    entry_quantity = int(
        position.get("entry_quantity")
        or (
            int(position.get("optionlot") or 0)
            * int(position.get("initial_lot") or position.get("lot") or 0)
        )
    )
    buy_quantity = int(position.get("buy_quantity") or 0)
    sell_quantity = int(position.get("sell_quantity") or 0)
    if not buy_quantity and not sell_quantity and entry_quantity:
        if (
            position.get("BSmode") is False
            or str(position.get("side") or "").upper() == "SELL"
        ):
            sell_quantity = entry_quantity
        else:
            buy_quantity = entry_quantity
    position.update({
        "entry_quantity": entry_quantity,
        "buy_quantity": buy_quantity,
        "sell_quantity": sell_quantity,
        "net_quantity": buy_quantity - sell_quantity,
        "is_open": position.get("status") == "open" and buy_quantity != sell_quantity,
    })
    return position


async def api_searchsymbol(request: Request, query: str = Query("", min_length=0)):
    payload = await payload_from_request(request) if request.method == "POST" else {}
    search = str(form_value(payload, "query", query)).strip().upper()
    if len(search) < 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query must be at least 3 characters long.")
    results = []
    seen = set()
    for filename in ("NSE_symbols.txt", "NFO_symbols.txt", "BSE_symbols.txt", "BFO_symbols.txt", "MCX_symbols.txt", "CDS_symbols.txt"):
        path = contract_file_path(filename)
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                for key in ("TradingSymbol", "Symbol", "Name"):
                    symbol = (row.get(key) or "").strip()
                    if symbol and search in symbol.upper() and symbol not in seen:
                        seen.add(symbol)
                        results.append(symbol)
                        break
                if len(results) >= 50:
                    break
        if len(results) >= 50:
            break
    if not results:
        common = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "CRUDEOIL"]
        results = [symbol for symbol in common if search in symbol]
    return ApiResponse(success=True, message="Symbols fetched", data={"results": results})


def api_index(user=Depends(get_current_user)):
    total_start = time.perf_counter()
    db = get_database()
    username = current_username(user)
    logger.info("api_index_start user=%s", username)

    step_start = time.perf_counter()
    broker_health = BrokerHealthService(db)
    active_broker = broker_health.active_broker(username)
    active_broker_health = broker_health.get_health(username, active_broker)
    logger.info(
        "api_index_broker_health_ms=%s user=%s broker=%s",
        _elapsed_ms(step_start),
        username,
        active_broker,
    )

    step_start = time.perf_counter()
    trading_runtime = WorkerControlService(db).get_status()
    logger.info(
        "api_index_trading_runtime_ms=%s user=%s",
        _elapsed_ms(step_start),
        username,
    )

    step_start = time.perf_counter()
    sub = db["subscriptionperiod"].find_one({"user": username})
    if not sub:
        ensure_free_subscription(username)
        sub = db["subscriptionperiod"].find_one({"user": username}) or {}
    try:
        active_subscription = datetime.datetime.strptime(sub.get("end", "1970-01-01"), "%Y-%m-%d") + datetime.timedelta(days=1) >= datetime.datetime.now()
    except ValueError:
        active_subscription = False
    logger.info(
        "api_index_subscription_ms=%s user=%s",
        _elapsed_ms(step_start),
        username,
    )

    step_start = time.perf_counter()
    strategies = [
        clean_document(doc)
        for doc in db["strategies"].find({
            "user": username,
            "status": {"$in": list(ACTIVE_STRATEGY_STATUSES)},
        })
    ]
    logger.info(
        "api_index_strategies_ms=%s count=%s user=%s",
        _elapsed_ms(step_start),
        len(strategies),
        username,
    )

    step_start = time.perf_counter()
    open_positions = [
        _position_with_quantities(doc)
        for doc in db["Opositions"].find({
            "user": username,
            "decision": "intrade",
            "status": "open",
        })
    ]
    logger.info(
        "api_index_opositions_ms=%s count=%s user=%s",
        _elapsed_ms(step_start),
        len(open_positions),
        username,
    )

    step_start = time.perf_counter()
    orders = [
        clean_document(doc)
        for doc in db["orders"].find({"user": username, "status": "opened"})
    ]
    closed_orders = [
        clean_document(doc)
        for doc in _latest_first(db["orders"].find({"user": username, "status": {"$ne": "opened"}}))
    ]
    logger.info(
        "api_index_orders_ms=%s open_count=%s closed_count=%s user=%s",
        _elapsed_ms(step_start),
        len(orders),
        len(closed_orders),
        username,
    )

    step_start = time.perf_counter()
    positions = [
        clean_document(doc)
        for doc in db["positions"].find({"user": username, "status": "open"})
    ]
    closed_positions = [
        clean_document(doc)
        for doc in _latest_first(db["positions"].find({"user": username, "status": {"$ne": "open"}}))
    ]
    logger.info(
        "api_index_positions_ms=%s open_count=%s closed_count=%s user=%s",
        _elapsed_ms(step_start),
        len(positions),
        len(closed_positions),
        username,
    )

    data = {
        "orders": orders,
        "closed_orders": closed_orders,
        "positions": positions,
        "closed_positions": closed_positions,
        "user": username,
        "allstrategies": {
            "Equity SSALGO": "add_eqssalgo_form",
            "MCX Commodity Strategy": "add_mcxstrategy_form",
            "143 Options": "add_ema_form",
            "Index FUTURE 143": "add_ema_fut_form",
            "Hedge Order": "add_fractalnubiatimehedgeorder_form",
            **({
                "EQUITY OPTIONS FUTURE": "add_ssequityfno_eq_form",
                "Chartink": "add_ssequity_eq_form",
                "SSALGO SSAUTO": "add_rf_form",
                "New 143": "add_sstrike_form",
                "SSALGOHF Options": "add_ssalgo_form",
                "Index FUTURE SSALGO": "add_ssalgo_fut_form",
            } if user.get("admin") else {}),
        },
        "strategy": strategies,
        "opositions": open_positions,
        "fixed": False,
        "userlog": active_broker_health.get("login_status") == "connected",
        "broker": active_broker,
        "broker_health": active_broker_health,
        "trading_runtime": trading_runtime,
        "user_subscription": active_subscription,
        "user_expiry": sub.get("end"),
        "adminuser": bool(user.get("admin")),
        "equity": "equity" in user,
        "brokers": {
            "AliceBlue": "https://ant.aliceblueonline.com",
            "Fyers": "https://login.fyers.in",
            "Shoonya": "https://trade.shoonya.com",
            "Zerodha": "https://kite.zerodha.com",
            "AngelOne": "https://smartapi.angelbroking.com",
            "Dhan": "https://web.dhan.co",
            "MOFS": "https://motilaloswal.com/login",
            "SMC": "https://www.smctrade.com/login.aspx",
        },
    }
    logger.info(
        "api_index_response_ready_ms=%s user=%s strategies=%s opositions=%s orders=%s positions=%s",
        _elapsed_ms(total_start),
        username,
        len(strategies),
        len(open_positions),
        len(orders),
        len(positions),
    )
    return response("Index data retrieved successfully", data)


async def api_delete_oposition(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    position_time = form_value(payload, "position_time")
    if not position_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Position time is required")
    result = collection("Opositions").update_one(
        {"entry_id": int(position_time), "user": current_username(user), "status": "open"},
        {"$set": {"decision": "exitit"}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")
    return response("Position updated successfully")


router = APIRouter(tags=["legacy dashboard"])

router.add_api_route("/api_searchsymbol", api_searchsymbol, methods=["GET", "POST"], response_model=ApiResponse)
router.add_api_route("/api_index", api_index, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_oposition", api_delete_oposition, methods=["POST"], response_model=ApiResponse)
