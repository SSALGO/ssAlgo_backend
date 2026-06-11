from app.api.legacy_compat.common import *
from app.domain.brokers.health import BrokerHealthService
from app.workers.control import WorkerControlService


async def api_searchsymbol(request: Request, query: str = Query("", min_length=0)):
    payload = await payload_from_request(request) if request.method == "POST" else {}
    search = str(form_value(payload, "query", query)).strip().upper()
    if len(search) < 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query must be at least 3 characters long.")
    results = []
    seen = set()
    for filename in ("NSE_symbols.txt", "NFO_symbols.txt", "BSE_symbols.txt", "BFO_symbols.txt", "MCX_symbols.txt", "CDS_symbols.txt"):
        path = BACKEND_ROOT / filename
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
    db = get_database()
    username = current_username(user)
    broker_health = BrokerHealthService(db)
    active_broker = broker_health.active_broker(username)
    active_broker_health = broker_health.get_health(username, active_broker)
    trading_runtime = WorkerControlService(db).get_status()
    sub = db["subscriptionperiod"].find_one({"user": username})
    if not sub:
        ensure_free_subscription(username)
        sub = db["subscriptionperiod"].find_one({"user": username}) or {}
    try:
        active_subscription = datetime.datetime.strptime(sub.get("end", "1970-01-01"), "%Y-%m-%d") + datetime.timedelta(days=1) >= datetime.datetime.now()
    except ValueError:
        active_subscription = False

    strategies = [
        clean_document(doc)
        for doc in db["strategies"].find({"user": username})
        if doc.get("status") in ACTIVE_STRATEGY_STATUSES
    ]
    open_positions = [
        clean_document(doc)
        for doc in db["Opositions"].find({"user": username})
        if doc.get("decision") == "intrade" and doc.get("status") == "open"
    ]
    data = {
        "orders": [clean_document(doc) for doc in db["orders"].find({"user": username}) if doc.get("status") == "opened"],
        "closed_orders": [clean_document(doc) for doc in db["orders"].find({"user": username}) if doc.get("status") != "opened"],
        "positions": [clean_document(doc) for doc in db["positions"].find({"user": username}) if doc.get("status") == "open"],
        "closed_positions": [clean_document(doc) for doc in db["positions"].find({"user": username}) if doc.get("status") != "open"],
        "user": username,
        "allstrategies": {
            "Equity SSALGO": "add_eqssalgo_form",
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
