import bcrypt
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status

from app.api.fastapi_auth import (
    create_compatible_access_token,
    decode_compatible_access_token,
    ensure_free_subscription,
    get_current_user,
    get_user_by_username_or_email,
    get_users_collection,
    verify_password,
)
from app.api.fastapi_schemas import (
    ApiResponse,
    BacktestRequest,
    BrokerCredentialsRequest,
    LoginRequest,
    OrderTransitionRequest,
    PaperOrderRequest,
    RegisterRequest,
)
from app.api.fastapi_services import FastAPITradingServices, get_trading_services
from app.core.database import get_database
from app.core.secrets import encrypt_secret_fields
from app.domain.audit.service import AuditLogService
from app.domain.brokers.adapters import BrokerCredentials, BrokerOrder
from app.domain.brokers.health import SECRET_FIELD_NAMES
from app.domain.brokers.registry import broker_payload
from app.realtime.dashboard import DashboardConnectionManager
from app.workers.control import WorkerControlService


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
broker_router = APIRouter(prefix="/api/brokers", tags=["brokers"])
paper_router = APIRouter(prefix="/api/paper", tags=["paper"])
order_router = APIRouter(prefix="/api/orders", tags=["orders"])
backtest_router = APIRouter(prefix="/api/backtests", tags=["backtests"])
legacy_router = APIRouter(tags=["legacy aliases"])
ws_router = APIRouter(tags=["websocket"])
dashboard_connections = DashboardConnectionManager()


def username(user):
    return user["username"]


def clean_mongo_document(doc):
    if not doc:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@auth_router.post("/login", response_model=ApiResponse)
def login(payload: LoginRequest):
    login_user = get_user_by_username_or_email(payload.username)
    if not login_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not verify_password(payload.password, login_user.get("password")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password")
    ensure_free_subscription(login_user["username"])
    access_token = create_compatible_access_token(login_user["username"])
    return ApiResponse(
        success=True,
        message="Successfully logged in",
        token=access_token,
        username=login_user["username"],
        access_token=access_token,
    )


@auth_router.post("/register", response_model=ApiResponse)
def register(payload: RegisterRequest):
    normalized_username = payload.username.strip().lower()
    normalized_email = payload.email.strip().lower()
    if not normalized_username or not normalized_email or not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing username, email, or password")
    users = get_users_collection()
    if users.find_one({"$or": [{"username": normalized_username}, {"email": normalized_email}]}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    users.insert_one({
        "username": normalized_username,
        "email": normalized_email,
        "mobile": payload.mobile,
        "password": bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()),
        "StrategyLimit": 10,
    })
    ensure_free_subscription(normalized_username)
    access_token = create_compatible_access_token(normalized_username)
    return ApiResponse(
        success=True,
        message="Successfully Registered & Logged in",
        token=access_token,
        username=normalized_username,
        access_token=access_token,
    )


@auth_router.post("/logout", response_model=ApiResponse)
def logout(_user=Depends(get_current_user)):
    return ApiResponse(success=True, message="Successfully logged out")


@broker_router.get("", response_model=ApiResponse)
def list_brokers(
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    payload = broker_payload()
    user_name = username(user)
    payload["current_broker"] = services.health.active_broker(user_name)
    payload["saved_credentials"] = services.health.saved_credentials(user_name)
    return ApiResponse(success=True, message="Brokers fetched", data=payload)


@broker_router.get("/status", response_model=ApiResponse)
def broker_status(
    broker: str = "",
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    data = (
        services.health.get_health(username(user), broker)
        if broker
        else services.health.list_health(username(user))
    )
    return ApiResponse(success=True, message="Broker status fetched", data=data)


@broker_router.post("/{broker}/credentials", response_model=ApiResponse)
def save_broker_credentials(
    broker: str,
    payload: BrokerCredentialsRequest,
    user=Depends(get_current_user),
):
    registry = broker_payload().get("broker_status", {}).get(broker, {})
    if registry.get("enabled") is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{broker} is not enabled for trading yet")
    db = get_database()
    values = dict(payload.values)
    existing = db["apis"].find_one({"user": username(user), "broker": broker}) or {}
    for field_name in list(values.keys()):
        if field_name in SECRET_FIELD_NAMES and not str(values.get(field_name, "")).strip() and existing.get(field_name):
            values.pop(field_name)
    values["user"] = username(user)
    values["broker"] = broker
    values = encrypt_secret_fields(values, SECRET_FIELD_NAMES)
    result = db["apis"].update_one(
        {"user": username(user), "broker": broker},
        {"$set": values},
        upsert=True,
    )
    if payload.activate:
        db["broker"].update_one(
            {"user": username(user)},
            {"$set": {"user": username(user), "selectedbroker": broker}},
            upsert=True,
        )
    AuditLogService(db).record(
        "broker_credentials_saved",
        user=username(user),
        resource_type="broker_api",
        resource_id=broker,
        details={"broker": broker, "activated": payload.activate},
    )
    return ApiResponse(
        success=True,
        message="Broker credentials saved",
        data={
            "broker": broker,
            "activated": payload.activate,
            "matched": result.matched_count,
            "modified": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        },
    )


@broker_router.get("/{broker}/credentials", response_model=ApiResponse)
def get_broker_credentials(
    broker: str,
    user=Depends(get_current_user),
):
    doc = get_database()["apis"].find_one({"user": username(user), "broker": broker}) or {}
    cleaned = clean_mongo_document(doc) or {}
    for field_name in SECRET_FIELD_NAMES:
        if field_name in cleaned:
            cleaned[field_name] = ""
    return ApiResponse(success=True, message="Broker credentials fetched", data=cleaned)


@paper_router.post("/orders", response_model=ApiResponse)
def place_paper_order(
    payload: PaperOrderRequest,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    order = BrokerOrder(
        user=username(user),
        broker="paper",
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        price=payload.price,
        strategy_id=payload.strategy_id,
        metadata=payload.metadata,
    )
    if not order.symbol:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="symbol is required")
    adapter = services.adapter_factory.create("paper")
    adapter.login(BrokerCredentials(user=username(user), broker="paper"))
    result = adapter.place_order(order)
    return ApiResponse(success=True, message="Paper order filled", data=result)


@order_router.get("", response_model=ApiResponse)
def list_orders(
    limit: int = 50,
    status_filter: str = Query("", alias="status"),
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    data = services.order_lifecycle.list_orders(username(user), limit=limit, status=status_filter or None)
    return ApiResponse(success=True, message="Orders fetched", data=data)


@order_router.get("/{order_id}", response_model=ApiResponse)
def get_order(
    order_id: str,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    data = services.order_lifecycle.get_order_for_user(order_id, username(user))
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return ApiResponse(success=True, message="Order fetched", data=data)


@order_router.post("/{order_id}/transition", response_model=ApiResponse)
def transition_order(
    order_id: str,
    payload: OrderTransitionRequest,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    try:
        data = services.order_lifecycle.transition_for_user(
            order_id,
            payload.status,
            username(user),
            {"source": "fastapi", "user": username(user), **payload.data},
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return ApiResponse(success=True, message="Order transitioned", data=data)


@backtest_router.post("/sma-crossover", response_model=ApiResponse)
def backtest_sma_crossover(
    payload: BacktestRequest,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    result = services.backtests.run_sma_crossover(
        candles=payload.candles,
        fast=payload.fast,
        slow=payload.slow,
        initial_capital=payload.initial_capital,
        quantity=payload.quantity,
    )
    result["user"] = username(user)
    return ApiResponse(success=True, message="Backtest completed", data=result)


async def legacy_payload(request: Request):
    if request.headers.get("content-type", "").startswith("application/json"):
        return await request.json()
    form = await request.form()
    return dict(form)


@legacy_router.post("/api_login", response_model=ApiResponse)
async def legacy_login(request: Request):
    payload = await legacy_payload(request)
    return login(LoginRequest(username=payload.get("username", ""), password=payload.get("password", "")))


@legacy_router.post("/api_register", response_model=ApiResponse)
async def legacy_register(request: Request):
    payload = await legacy_payload(request)
    return register(RegisterRequest(
        username=payload.get("username", ""),
        email=payload.get("email", ""),
        password=payload.get("password", ""),
        mobile=payload.get("mobile", ""),
    ))


@legacy_router.api_route("/api_logout", methods=["GET", "POST"], response_model=ApiResponse)
def legacy_logout(_user=Depends(get_current_user)):
    return logout()


@legacy_router.post("/api_broker_status", response_model=ApiResponse)
def legacy_broker_status(
    broker: str = "",
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    data = services.health.get_health(username(user), broker) if broker else services.health.list_health(username(user))
    return ApiResponse(success=True, message="Broker status fetched", data=data)


@legacy_router.post("/api_paper_order", response_model=ApiResponse)
async def legacy_paper_order(
    request: Request,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    payload = await legacy_payload(request)
    return place_paper_order(PaperOrderRequest(**payload), user=user, services=services)


@legacy_router.post("/api_order_lifecycle", response_model=ApiResponse)
async def legacy_order_lifecycle(
    request: Request,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    payload = await legacy_payload(request)
    operation = payload.get("operation", "list")
    if operation == "get":
        data = services.order_lifecycle.get_order_for_user(payload.get("order_id", ""), username(user))
        if not data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    elif operation == "transition":
        try:
            data = services.order_lifecycle.transition_for_user(
                payload.get("order_id", ""),
                payload.get("status", ""),
                username(user),
                {"source": "fastapi_legacy_alias", "user": username(user)},
            )
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    else:
        data = services.order_lifecycle.list_orders(
            username(user),
            limit=int(payload.get("limit", 50)),
            status=payload.get("status") or None,
        )
    return ApiResponse(success=True, message="Order lifecycle fetched", data=data)


@legacy_router.post("/api_backtest", response_model=ApiResponse)
async def legacy_backtest(
    request: Request,
    user=Depends(get_current_user),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    payload = await legacy_payload(request)
    if isinstance(payload.get("candles"), str):
        import json
        payload["candles"] = json.loads(payload["candles"])
    return backtest_sma_crossover(BacktestRequest(**payload), user=user, services=services)


@ws_router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        claims = decode_compatible_access_token(token)
    except Exception:
        await websocket.close(code=1008)
        return
    user = get_users_collection().find_one({"username": claims.get("sub")})
    if not user:
        await websocket.close(code=1008)
        return
    user_name = username(user)
    await dashboard_connections.connect(user_name, websocket)
    services = get_trading_services()
    try:
        await websocket.send_json({
            "type": "broker_health",
            "data": services.health.list_health(user_name),
        })
        await websocket.send_json({
            "type": "worker_status",
            "data": WorkerControlService(services.db).get_status(),
        })
        while True:
            message = await websocket.receive_text()
            payload = dashboard_connections.parse_message(message)
            message_type = payload.get("type")
            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif message_type == "broker_health":
                await websocket.send_json({
                    "type": "broker_health",
                    "data": services.health.list_health(user_name),
                })
            elif message_type == "worker_status":
                await websocket.send_json({
                    "type": "worker_status",
                    "data": WorkerControlService(services.db).get_status(),
                })
            else:
                await websocket.send_json({"type": "error", "message": "Unsupported dashboard message"})
    except WebSocketDisconnect:
        dashboard_connections.disconnect(user_name, websocket)
        return
