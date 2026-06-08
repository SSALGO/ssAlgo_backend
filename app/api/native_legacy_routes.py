import csv
import datetime
import json
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

import razorpay
from bs4 import BeautifulSoup
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.fastapi_auth import create_compatible_access_token, ensure_free_subscription, get_current_user, require_admin
from app.api.fastapi_schemas import ApiResponse
from app.core.config import AppConfig
from app.core.database import get_database
from app.domain.brokers.health import SECRET_FIELD_NAMES
from app.domain.brokers.registry import broker_payload
from models import (
    EMA_fut_mode,
    EMA_mode,
    EQSSALGO_mode,
    FRACTALNUBIATIMEHEDGEORDER_mode,
    PEMA_fut_mode,
    PEMA_mode,
    RF_mode,
    SSALGO_fut_mode,
    SSALGO_mode,
    SSAUTO_fut_mode,
    SSAUTO_mode,
    SSEQUITYFNO_EQ_mode,
    SSEQUITY_EQ_mode,
    SSEQUITY_fut_mode,
    SSEQUITY_mode,
    SSTRIKE_mode,
)


native_legacy_router = APIRouter(tags=["native legacy api"])

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_STRATEGY_STATUSES = {"opened", "paused"}
PLAN_PRICES = {
    "1 Month": (299900, 30),
    "3 Months": (854715, 90),
    "6 Months": (1620000, 180),
    "12 Months": (3060000, 365),
    "12_month": (599900, 365),
    "13month": (649900, 365),
    "LIFETIME": (9999900, 3650 * 5),
}

ADD_STRATEGY_ROUTES = {
    "/api_add_ssalgo": ("ssalgo", "SSALGO strategy added successfully"),
    "/api_add_rf": ("rf", "RF strategy added successfully"),
    "/api_add_ssequity_fut": ("ssequity_fut", "FUT SSEQUITY strategy added successfully"),
    "/api_add_ssequity": ("ssequity", "SSEQUITY strategy added successfully"),
    "/api_add_ssequity_eq": ("ssequity_eq", "EQ SSEQUITY strategy added successfully"),
    "/api_add_ssequityfno_eq": ("ssequityfno_eq", "EQ SSEQUITY FNO strategy added successfully"),
    "/api_add_sstrike": ("sstrike", "SSTRIKE strategy added successfully"),
    "/api_add_ssalgo_fut": ("ssalgo_fut", "FUT SSALGO strategy added successfully"),
    "/api_add_ssauto": ("ssauto", "SSAUTO strategy added successfully"),
    "/api_add_ssauto_fut": ("ssauto_fut", "FUT SSAUTO strategy added successfully"),
    "/api_add_ema": ("ema", "EMA strategy added successfully"),
    "/api_add_pema": ("pema", "PEMA strategy added successfully"),
    "/api_add_pema_fut": ("pema_fut", "FUT PEMA strategy added successfully"),
    "/api_add_ema_fut": ("ema_fut", "FUT EMA strategy added successfully"),
    "/api_add_eqssalgo": ("eqssalgo", "EQSSALGO strategy added successfully"),
    "/api_add_fractalnubiatimehedgeorder": (
        "fractalnubiatimehedgeorder",
        "FRACTALNUBIATIMEHEDGEORDER strategy added successfully",
    ),
}

EDIT_STRATEGY_ROUTES = {
    "/api_edit_rf": ("rf", "RF strategy updated successfully"),
    "/api_edit_ssalgo": ("ssalgo", "SSALGO strategy updated successfully"),
    "/api_edit_fractalnubiatimehedgeorder": (
        "fractalnubiatimehedgeorder",
        "FRACTALNUBIATIMEHEDGEORDER strategy updated successfully",
    ),
    "/api_edit_eqssalgo": ("eqssalgo", "EQSSALGO strategy updated successfully"),
    "/api_edit_ssauto": ("ssauto", "SSAUTO strategy updated successfully"),
    "/api_edit_ssequity": ("ssequity_eq", "SSEQUITY strategy updated successfully"),
    "/api_edit_ssequityfno": ("ssequityfno_eq", "SSEQUITY FNO strategy updated successfully"),
    "/api_edit_sstrike": ("sstrike", "SSTRIKE strategy updated successfully"),
    "/api_edit_ema": ("ema", "EMA strategy updated successfully"),
    "/api_edit_pema": ("pema", "PEMA strategy updated successfully"),
}

ADMIN_EDIT_STRATEGY_ROUTES = {
    "/api_edit_admin_eqssalgo": ("eqssalgo", "EQSSALGO strategy updated successfully"),
    "/api_edit_admin_ssalgo": ("ssalgo", "SSALGO strategy edited successfully"),
    "/api_edit_admin_ssauto": ("ssauto", "SSAUTO strategy edited successfully"),
    "/api_edit_admin_ema": ("ema", "EMA strategy edited successfully"),
    "/api_edit_admin_pema": ("pema", "PEMA strategy edited successfully"),
    "/api_edit_admin_sstrike": ("sstrike", "SSTRIKE strategy edited successfully"),
    "/api_edit_admin_ssequityfno": ("ssequityfno_eq", "SSEQUITY FNO strategy edited successfully"),
    "/api_edit_admin_ssequity": ("ssequity_eq", "SSEQUITY strategy edited successfully"),
    "/api_edit_admin_rf": ("rf", "RF strategy edited successfully"),
}


def current_username(user):
    return user["username"]


def collection(name):
    return get_database()[name]


def response(message, data=None, success=True):
    return ApiResponse(success=success, message=message, data=data)


def object_id(value, field_name="id"):
    if not ObjectId.is_valid(str(value or "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name} format")
    return ObjectId(str(value))


def to_jsonable(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


def clean_document(doc, *, hide_password=False, mask_secrets=False):
    if not doc:
        return None
    cleaned = to_jsonable(dict(doc))
    if "_id" in cleaned:
        cleaned["id"] = cleaned.get("id") or cleaned["_id"]
    if hide_password:
        cleaned.pop("password", None)
        cleaned.pop("reset_token", None)
        cleaned.pop("otp", None)
        cleaned.pop("otp_expiration", None)
    if mask_secrets:
        for field_name in SECRET_FIELD_NAMES:
            if field_name in cleaned:
                cleaned[field_name] = ""
    return cleaned


async def payload_from_request(request: Request):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    form = await request.form()
    payload = {}
    for key, value in form.multi_items():
        if hasattr(value, "filename"):
            continue
        if key in payload:
            if not isinstance(payload[key], list):
                payload[key] = [payload[key]]
            payload[key].append(value)
        else:
            payload[key] = value
    return payload


def form_value(payload, key, default=""):
    value = payload.get(key, default)
    if isinstance(value, list):
        value = value[-1] if value else default
    if value is None:
        return default
    return value.strip() if isinstance(value, str) else value


def form_list(payload, key):
    value = payload.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value).strip()] if str(value).strip() else []


def flat_form(payload):
    flattened = {}
    for key, value in payload.items():
        if isinstance(value, list):
            flattened[key] = [str(item) for item in value]
        elif isinstance(value, bool):
            flattened[key] = "true" if value else "false"
        elif value is None:
            flattened[key] = ""
        else:
            flattened[key] = str(value)
    return flattened


def active_strategy_units(username):
    count = 0
    for strategy in collection("strategies").find({"user": username}):
        if strategy.get("status") not in ACTIVE_STRATEGY_STATUSES:
            continue
        symbol = strategy.get("symbol")
        if isinstance(symbol, list):
            count += len(symbol)
        elif symbol:
            count += 1
    return count


def create_botcode_for_user(user, botname):
    created_at_ms = int(datetime.datetime.now().timestamp() * 1000)
    unique_suffix = secrets.token_hex(3)
    return "{}_{}_{}_{}_{}".format(
        botname,
        str(user.get("_id", "")),
        created_at_ms,
        user.get("mobile", ""),
        unique_suffix,
    )


def select_strategy_model(kind, payload):
    if kind == "ssalgo":
        return SSALGO_fut_mode if "onspot" in payload else SSALGO_mode
    if kind == "ssauto":
        return SSAUTO_fut_mode if "onspot" in payload else SSAUTO_mode
    if kind == "ema":
        return EMA_fut_mode if "onspot" in payload else EMA_mode
    if kind == "pema":
        return PEMA_fut_mode if "onspot" in payload else PEMA_mode
    return {
        "rf": RF_mode,
        "ssequity_fut": SSEQUITY_fut_mode,
        "ssequity": SSEQUITY_mode,
        "ssequity_eq": SSEQUITY_EQ_mode,
        "ssequityfno_eq": SSEQUITYFNO_EQ_mode,
        "sstrike": SSTRIKE_mode,
        "ssalgo_fut": SSALGO_fut_mode,
        "ssauto_fut": SSAUTO_fut_mode,
        "ema_fut": EMA_fut_mode,
        "pema_fut": PEMA_fut_mode,
        "eqssalgo": EQSSALGO_mode,
        "fractalnubiatimehedgeorder": FRACTALNUBIATIMEHEDGEORDER_mode,
    }[kind]


def normalize_strategy_payload(kind, payload, user, *, existing=None, admin=False):
    data = flat_form(payload)
    target_user = existing.get("user") if admin and existing else current_username(user)
    data["user"] = target_user
    data.pop("token", None)
    if "botcode" not in data and existing:
        data["botcode"] = existing.get("botcode", "")
    if "botcode" not in data or not data["botcode"]:
        data["botcode"] = create_botcode_for_user(user, data.get("botname", str(int(datetime.datetime.now().timestamp()))))
    if kind == "eqssalgo":
        symbols = form_list(payload, "symbol[]") or form_list(payload, "symbol")
        data["symbol"] = symbols
        data["symbol[]"] = symbols
    if kind == "fractalnubiatimehedgeorder":
        options = form_list(payload, "ooption")
        strikes = form_list(payload, "ostrike")
        sides = form_list(payload, "oside")
        expiries = form_list(payload, "oexpiry")
        lots = form_list(payload, "olot")
        if any([options, strikes, sides, expiries, lots]):
            if not (len(options) == len(strikes) == len(sides) == len(expiries) == len(lots)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mismatched option leg data lengths")
            data["legs"] = [
                {"option": option, "strike": strike, "side": side, "expiry": expiry, "lot": lot}
                for option, strike, side, expiry, lot in zip(options, strikes, sides, expiries, lots)
            ]
        elif existing and existing.get("legs"):
            data["legs"] = existing["legs"]
        else:
            data["legs"] = []
        data.setdefault("exittime", str(int(datetime.datetime.now().timestamp())))
        methods = form_list(payload, "method")
        if methods:
            data["method"] = methods[-1]
    return data


def build_strategy(kind, payload, user, *, existing=None, admin=False):
    data = normalize_strategy_payload(kind, payload, user, existing=existing, admin=admin)
    try:
        obj = select_strategy_model(kind, data)(data)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing strategy field: {exc.args[0]}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid strategy field value: {exc}")
    result = dict(obj.__dict__)
    if kind == "eqssalgo":
        result["symbol[]"] = data.get("symbol[]", [])
    return result


def fractal_reset_update(botcode, username=None, set_fields=None):
    query = {"botcode": botcode}
    if username:
        query["user"] = username
    strategy = collection("strategies").find_one(query)
    update = {"$set": dict(set_fields or {})}
    if strategy and strategy.get("strategy") == "FRACTALNUBIATIMEHEDGEORDER":
        open_query = {"botcode": botcode, "status": "open"}
        if username:
            open_query["user"] = username
        has_open_position = collection("Opositions").count_documents(open_query, limit=1) > 0
        if not has_open_position:
            update["$unset"] = {
                "fractal_fire_state": "",
                "fractal_fire_time": "",
                "fractal_fire_reason": "",
            }
            update["$set"]["position"] = "out"
    return update


def mark_strategy_positions_exit(botcode, username=None):
    query = {"botcode": botcode, "status": "open"}
    if username:
        query["user"] = username
    collection("Opositions").update_many(query, {"$set": {"decision": "exitit"}})


@lru_cache(maxsize=1)
def strategy_forms():
    forms = {}
    templates_dir = BACKEND_ROOT / "stemplates"
    jinja_pattern = re.compile(r"{%.*?%}|{{.*?}}")

    def clean_jinja(value):
        return jinja_pattern.sub("", value or "").strip()

    for name in [
        "ema",
        "ema_fut",
        "rf",
        "ssalgo",
        "ssalgo_fut",
        "ssequity_eq",
        "ssequityfno_eq",
        "sstrike",
        "eqssalgo",
        "fractalnubiatimehedgeorder",
    ]:
        path = templates_dir / f"{name}_form.html"
        if not path.exists():
            forms[f"add_{name}_form.html"] = []
            continue
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")

        def label_for(input_id):
            label = soup.find("label", {"for": input_id})
            return label.text.strip() if label else "Unnamed Field"

        tags = []
        for tag in soup.find_all(["input", "select"]):
            tag_dict = {"tag": tag.name}
            for attr, value in tag.attrs.items():
                if isinstance(value, str):
                    cleaned_value = clean_jinja(value)
                    if cleaned_value:
                        tag_dict[attr] = cleaned_value
                else:
                    tag_dict[attr] = value
            field_name = tag_dict.get("name")
            if not field_name or field_name in {"ooption", "ostrike", "oside", "oexpiry", "olot"}:
                continue
            tag_dict["label"] = label_for(tag.get("id", ""))
            if tag.name == "input" and tag.attrs.get("type") in {"hidden", "checkbox", "radio"}:
                tag_dict.pop("required", None)
            if tag.name == "select":
                tag_dict["options"] = [
                    {"value": clean_jinja(option.get("value", "")), "text": clean_jinja(option.text)}
                    for option in tag.find_all("option")
                ]
            tag_dict.pop("class", None)
            tag_dict.pop("id", None)
            tags.append(tag_dict)
        if name == "fractalnubiatimehedgeorder":
            table = soup.find("table", {"id": "optionsTable"})
            row_template = []
            if table and table.find("tbody") and table.find("tbody").find("tr"):
                for td in table.find("tbody").find("tr").find_all("td"):
                    input_tag = td.find(["input", "select"])
                    if input_tag:
                        field = {"tag": input_tag.name}
                        for attr, value in input_tag.attrs.items():
                            if isinstance(value, str):
                                cleaned = clean_jinja(value)
                                if cleaned:
                                    field[attr] = cleaned
                            else:
                                field[attr] = value
                        if input_tag.name == "select":
                            field["options"] = [
                                {"value": clean_jinja(option.get("value", "")), "text": clean_jinja(option.text)}
                                for option in input_tag.find_all("option")
                            ]
                        field.pop("class", None)
                        field.pop("id", None)
                        row_template.append(field)
                    else:
                        row_template.append({"tag": "td", "text": td.get_text(strip=True)})
            tags.append({"tag": "table", "children": row_template})
        forms[f"add_{name}_form.html"] = tags
    return forms


def strategy_form_page(order):
    algo = str(order.get("strategy", "")).lower()
    if "ssequity" in algo:
        return f"add_{algo}_eq_form.html"
    if "onspot" in order:
        return f"add_{algo}_fut_form.html"
    return f"add_{algo}_form.html"


def price_plan(price_label):
    for label, plan in PLAN_PRICES.items():
        if label in str(price_label or ""):
            return plan
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription plan")


def require_razorpay_client():
    if not AppConfig.RAZORPAY_KEY_ID or not AppConfig.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Razorpay credentials are not configured")
    return razorpay.Client(auth=(AppConfig.RAZORPAY_KEY_ID, AppConfig.RAZORPAY_KEY_SECRET))


def extend_subscription(username, duration):
    duration = int(duration)
    subs = collection("subscriptionperiod")
    existing = subs.find_one({"user": username})
    today = datetime.datetime.now().date()
    if existing:
        try:
            current_end = datetime.datetime.strptime(existing["end"], "%Y-%m-%d")
        except Exception:
            current_end = datetime.datetime.combine(today, datetime.time.min)
        base = current_end if current_end >= datetime.datetime.now() else datetime.datetime.now()
        start = existing.get("start") or today.strftime("%Y-%m-%d")
        end = base + datetime.timedelta(days=duration)
        data = {"user": username, "start": start, "end": end.strftime("%Y-%m-%d"), "subtype": "paid"}
        subs.update_one({"user": username}, {"$set": data})
        return data
    end = today + datetime.timedelta(days=duration)
    data = {"user": username, "start": today.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "subtype": "paid"}
    subs.insert_one(data)
    return data


def historical_rows(username, start_ts, end_ts, include_pnl=False):
    rows = []
    pnl = 0
    cursor = collection("Opositions").find({"user": username, "status": "close", "time": {"$gte": start_ts, "$lte": end_ts}})
    for row in cursor.sort("_id", -1):
        cleaned = clean_document(row) or {}
        if include_pnl:
            pnl += float(cleaned.get("pnl") or 0)
            offset = datetime.timedelta(hours=5, minutes=30)
            for field in ("time", "exittime"):
                if isinstance(row.get(field), (int, float)):
                    cleaned[field] = str((datetime.datetime.utcfromtimestamp(row[field]) + offset).time())
        rows.append(cleaned)
    return (rows, pnl) if include_pnl else rows


def parse_date_range(start_date, end_date):
    selected_start = start_date or str(datetime.datetime.now().date())
    selected_end = end_date or str(datetime.datetime.now().date())
    try:
        start = datetime.datetime.strptime(selected_start, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid start date format. Please use YYYY-MM-DD.")
    try:
        end = datetime.datetime.strptime(selected_end, "%Y-%m-%d") + datetime.timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid end date format. Please use YYYY-MM-DD.")
    return selected_start, selected_end, int(start.timestamp()), int(end.timestamp())


@native_legacy_router.api_route("/api_searchsymbol", methods=["GET", "POST"], response_model=ApiResponse)
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


@native_legacy_router.post("/api_index", response_model=ApiResponse)
def api_index(user=Depends(get_current_user)):
    db = get_database()
    username = current_username(user)
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
        "userlog": False,
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


@native_legacy_router.post("/api_delete_oposition", response_model=ApiResponse)
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


@native_legacy_router.api_route("/api_user_profile", methods=["GET", "POST"], response_model=ApiResponse)
def api_user_profile(user=Depends(get_current_user)):
    username = current_username(user)
    profile = clean_document(user, hide_password=True) or {}
    profile.pop("_id", None)
    profile.setdefault("day_profit_limit", "25000")
    profile.setdefault("day_loss_limit", "25000")
    profile.setdefault("trade_limit", "100")
    sub = collection("subscriptionperiod").find_one({"user": username})
    profile["end"] = sub.get("end", "None") if sub else "None"
    profile["subtype"] = sub.get("subtype", "None") if sub else "None"
    profile["StrategyRemaining"] = int(profile.get("StrategyLimit", 10)) - active_strategy_units(username)
    return response("Complete User Profile", {key: str(value) for key, value in profile.items()})


@native_legacy_router.post("/api_pricing", response_model=ApiResponse)
def api_pricing():
    plans = [
        ["1 Month", 2999, 2999],
        ["3 Months", 9000, 8547],
        ["6 Months", 18000, 16200],
        ["12 Months", 36000, 30600],
        ["LIFETIME", 360000, 99999],
    ]
    return response("Successfully Fetched Pricing Plans", plans)


@native_legacy_router.post("/api_pay", response_model=ApiResponse)
async def api_pay(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    amount, days = price_plan(form_value(payload, "price"))
    client = require_razorpay_client()
    payment = client.order.create(data={"amount": amount, "currency": "INR", "receipt": "#11"})
    return ApiResponse(
        success=True,
        message="Payment order created",
        data={
            "name": user["username"],
            "email": user.get("email", ""),
            "ph_nm": user.get("mobile", ""),
            "duration": days,
            "payment": payment,
        },
        token=None,
    ).model_copy(update={"data": {
        "name": user["username"],
        "email": user.get("email", ""),
        "ph_nm": user.get("mobile", ""),
        "duration": days,
        "payment": payment,
        "key": AppConfig.RAZORPAY_KEY_ID,
    }})


@native_legacy_router.post("/api_pay_verify", response_model=ApiResponse)
async def api_pay_verify(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    params = {
        "razorpay_order_id": form_value(payload, "order_id"),
        "razorpay_payment_id": form_value(payload, "payment_id"),
        "razorpay_signature": form_value(payload, "signature"),
    }
    duration = form_value(payload, "duration")
    if not all(params.values()) or not duration:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment id, order id, signature, and duration are required")
    client = require_razorpay_client()
    verified = client.utility.verify_payment_signature(params)
    collection("payreceipt").insert_one({
        "time": datetime.datetime.now(),
        "user": current_username(user),
        "order_id": params["razorpay_order_id"],
        "payment_id": params["razorpay_payment_id"],
        "status": verified,
    })
    subscription = extend_subscription(current_username(user), duration)
    return response("Payment verified successfully", {"subscription": clean_document(subscription)})


@native_legacy_router.post("/api_pay_fail", response_model=ApiResponse)
def api_pay_fail(_user=Depends(get_current_user)):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment couldn't go through and failed due to some reason.")


@native_legacy_router.post("/api_historicalbacktest", response_model=ApiResponse)
def api_historicalbacktest(
    start_date: str = Query("", alias="start_date"),
    date: str = Query("", alias="date"),
    end_date: str = Query(""),
    user=Depends(get_current_user),
):
    selected_start, selected_end, start_ts, end_ts = parse_date_range(start_date or date, end_date)
    history, pnl = historical_rows(current_username(user), start_ts, end_ts, include_pnl=True)
    return response("Successfully Fetched User History", {
        "history": history,
        "selected_start_date": selected_start,
        "selected_end_date": selected_end,
        "pnl": pnl,
    })


@native_legacy_router.get("/api_mainhistoricalbacktest", response_model=ApiResponse)
def api_mainhistoricalbacktest(
    start_date: str = Query("", alias="start_date"),
    date: str = Query("", alias="date"),
    end_date: str = Query(""),
    _user=Depends(get_current_user),
):
    selected_start, selected_end, start_ts, end_ts = parse_date_range(start_date or date, end_date)
    history = historical_rows("kinguniverse129", start_ts, end_ts)
    return response("Successfully Fetched Main History", {
        "history": history,
        "selected_start_date": selected_start,
        "selected_end_date": selected_end,
    })


@native_legacy_router.post("/api_users", response_model=ApiResponse)
def api_users(_admin=Depends(require_admin)):
    users = []
    for user in collection("users").find({}):
        if "StrategyLimit" not in user:
            user["StrategyLimit"] = 10
            collection("users").update_one({"username": user["username"]}, {"$set": {"StrategyLimit": 10}})
        cleaned = clean_document(user, hide_password=True)
        cleaned.pop("_id", None)
        users.append(cleaned)
    return response("Users fetched successfully", users)


@native_legacy_router.post("/api_update_user/{user_id}", response_model=ApiResponse)
async def api_update_user(user_id: str, request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    data = {
        "username": form_value(payload, "username"),
        "email": form_value(payload, "email"),
        "mobile": form_value(payload, "mobile"),
        "StrategyLimit": form_value(payload, "StrategyLimit", 10),
    }
    collection("users").update_one({"_id": object_id(user_id, "user_id")}, {"$set": data})
    return response("updated Successfully User", data)


@native_legacy_router.post("/api_delete_user/{user_id}", response_model=ApiResponse)
def api_delete_user(user_id: str, _admin=Depends(require_admin)):
    result = collection("users").delete_one({"_id": object_id(user_id, "user_id")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return response("Successfully Deleted User")


@native_legacy_router.post("/api_apis", response_model=ApiResponse)
def api_apis(_admin=Depends(require_admin)):
    data = [clean_document(doc, mask_secrets=True) for doc in collection("apis").find({})]
    return response("Fetched Successfully APIs", data)


@native_legacy_router.post("/api_strategys", response_model=ApiResponse)
def api_strategys(_admin=Depends(require_admin)):
    data = [
        clean_document(doc)
        for doc in collection("strategies").find({})
        if doc.get("status") in ACTIVE_STRATEGY_STATUSES
    ]
    return response("Fetched Successfully Strategies", data)


@native_legacy_router.post("/api_get_api", response_model=ApiResponse)
def api_get_api(user=Depends(get_current_user)):
    api = collection("apis").find_one({"user": current_username(user)})
    if not api:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")
    return response("Fetched Successfully API", clean_document(api, mask_secrets=True))


@native_legacy_router.post("/api_update_api", response_model=ApiResponse)
async def api_update_api(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    api_id = form_value(payload, "id")
    data = {
        "apikey": form_value(payload, "apikey"),
        "apisecret": form_value(payload, "apisecret"),
        "user": form_value(payload, "user") or form_value(payload, "token"),
    }
    if "auth_code" in payload:
        data["auth_code"] = form_value(payload, "auth_code")
    collection("apis").update_one({"_id": object_id(api_id)}, {"$set": data})
    return response("Fetched Successfully Updated API")


@native_legacy_router.post("/api_add_apikey", response_model=ApiResponse)
@native_legacy_router.post("/api_add_multi_apikey", response_model=ApiResponse)
async def api_add_apikey(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    data = flat_form(payload)
    data["user"] = current_username(user)
    data.pop("token", None)
    inserted_id = collection("apis").insert_one(data).inserted_id
    return response("API key added successfully", {"id": str(inserted_id)})


@native_legacy_router.post("/api_edit_apikey", response_model=ApiResponse)
@native_legacy_router.post("/api_edit_multi_apikey", response_model=ApiResponse)
async def api_edit_apikey(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    data = flat_form(payload)
    data["user"] = current_username(user)
    api_id = data.pop("id", "")
    data.pop("token", None)
    query = {"_id": object_id(api_id)} if api_id else {"user": current_username(user), "broker": data.get("broker")}
    result = collection("apis").update_one(query, {"$set": data}, upsert=not api_id)
    return response("API key updated successfully", {
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted_id": str(result.upserted_id) if result.upserted_id else None,
    })


@native_legacy_router.post("/api_multi_api", response_model=ApiResponse)
async def api_multi_api(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    operation = str(form_value(payload, "operation", "get")).lower()
    broker = form_value(payload, "broker")
    if not broker:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="broker is required")
    query = {"user": current_username(user), "broker": broker}
    if operation == "get":
        api = collection("apis").find_one(query)
        if not api:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")
        return response("Fetched Successfully API", clean_document(api, mask_secrets=True))
    if operation == "update":
        data = flat_form(payload)
        data["user"] = current_username(user)
        data.pop("token", None)
        data.pop("operation", None)
        result = collection("apis").update_one(query, {"$set": data}, upsert=True)
        message = "Successfully Created API" if result.upserted_id else "Successfully Updated API"
        return response(message, {
            "matched": result.matched_count,
            "modified": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        })
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid operation: {operation}")


@native_legacy_router.post("/api_broker_multi_api", response_model=ApiResponse)
async def api_broker_multi_api(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    username = current_username(user)
    registry = broker_payload()
    broker_data = collection("broker").find_one({"user": username})
    current_broker = (broker_data or {}).get("selectedbroker", "aliceblue")
    if not broker_data:
        collection("broker").update_one({"user": username}, {"$set": {"user": username, "selectedbroker": current_broker}}, upsert=True)
    requested_broker = form_value(payload, "selectedbroker")
    data = {
        "broker_requirements": registry["broker_requirements"],
        "broker_actions": registry["broker_actions"],
        "broker_display_names": registry["broker_display_names"],
        "broker_status": registry["broker_status"],
        "current_broker": current_broker,
    }
    if requested_broker and requested_broker in registry["broker_requirements"]:
        data = {
            "broker_requirements": {requested_broker: registry["broker_requirements"][requested_broker]},
            "broker_actions": {requested_broker: registry["broker_actions"].get(requested_broker, {})},
            "broker_display_names": {requested_broker: registry["broker_display_names"].get(requested_broker, requested_broker)},
            "broker_status": {requested_broker: registry["broker_status"].get(requested_broker, {})},
            "current_broker": current_broker,
        }
    return response("Successfully fetched broker requirements", data)


@native_legacy_router.post("/api_delete_api", response_model=ApiResponse)
async def api_delete_api(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    api_id = form_value(payload, "id")
    result = collection("apis").delete_one({"_id": object_id(api_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API ID not found")
    return response("API Key Deleted Successfully")


@native_legacy_router.post("/api_admin", response_model=ApiResponse)
def api_admin(_admin=Depends(require_admin)):
    data = {
        "controls": [clean_document(doc) for doc in collection("admincontrol").find({})],
        "strategyco": [clean_document(doc) for doc in collection("strategyinput").find({})],
    }
    return response("Successfully fetched Admin Page", data)


@native_legacy_router.post("/api_subscription", response_model=ApiResponse)
def api_subscription(_admin=Depends(require_admin)):
    data = [clean_document(doc) for doc in collection("subscriptionperiod").find({})]
    return response("Successfully fetched subscription data.", data)


@native_legacy_router.post("/api_create_subscription", response_model=ApiResponse)
async def api_create_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    data = {
        "user": form_value(payload, "user"),
        "start": form_value(payload, "start"),
        "end": form_value(payload, "end"),
        "subtype": form_value(payload, "subtype"),
    }
    if not all(data.values()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All fields are required.")
    inserted_id = collection("subscriptionperiod").insert_one(data).inserted_id
    return response("Successfully created subscription.", str(inserted_id))


@native_legacy_router.post("/api_get_subscription", response_model=ApiResponse)
async def api_get_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    subscription_id = form_value(payload, "id")
    subscription = collection("subscriptionperiod").find_one({"_id": object_id(subscription_id)})
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    return response("Successfully fetched subscription.", clean_document(subscription))


@native_legacy_router.post("/api_update_subscription", response_model=ApiResponse)
async def api_update_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    subscription_id = form_value(payload, "id")
    data = {
        "start": form_value(payload, "start"),
        "end": form_value(payload, "end"),
        "subtype": form_value(payload, "subtype"),
    }
    if not all(data.values()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All fields are required.")
    result = collection("subscriptionperiod").update_one({"_id": object_id(subscription_id)}, {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    return response("Successfully updated subscription.")


@native_legacy_router.post("/api_delete_subscription", response_model=ApiResponse)
async def api_delete_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    result = collection("subscriptionperiod").delete_one({"_id": object_id(form_value(payload, "id"))})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription ID not found")
    return response("Subscription  Deleted Successfully")


@native_legacy_router.post("/api_add_strategy_form", response_model=ApiResponse)
@native_legacy_router.post("/api_add_strategy_form/", response_model=ApiResponse)
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


@native_legacy_router.post("/api_edit_strategy_form/{order_time}", response_model=ApiResponse)
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


@native_legacy_router.post("/api_edit_admin_strategy_form/{order_time}", response_model=ApiResponse)
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


@native_legacy_router.post("/api_edit_strategyinput", response_model=ApiResponse)
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


@native_legacy_router.post("/api_edit_strategyinput_form", response_model=ApiResponse)
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
        return response(message, {"botcode": botcode})

    endpoint.__name__ = f"{'admin_' if admin else ''}edit_{kind}_strategy"
    return endpoint


for route_path, (strategy_kind, success_message) in ADD_STRATEGY_ROUTES.items():
    native_legacy_router.add_api_route(
        route_path,
        make_add_strategy_endpoint(strategy_kind, success_message),
        methods=["POST"],
        response_model=ApiResponse,
    )

for route_path, (strategy_kind, success_message) in EDIT_STRATEGY_ROUTES.items():
    native_legacy_router.add_api_route(
        route_path,
        make_edit_strategy_endpoint(strategy_kind, success_message),
        methods=["POST"],
        response_model=ApiResponse,
    )

for route_path, (strategy_kind, success_message) in ADMIN_EDIT_STRATEGY_ROUTES.items():
    native_legacy_router.add_api_route(
        route_path,
        make_edit_strategy_endpoint(strategy_kind, success_message, admin=True),
        methods=["POST"],
        response_model=ApiResponse,
    )
    native_legacy_router.add_api_route(
        f"{route_path}/",
        make_edit_strategy_endpoint(strategy_kind, success_message, admin=True),
        methods=["POST"],
        response_model=ApiResponse,
    )


@native_legacy_router.post("/api_stop_ssalgo", response_model=ApiResponse)
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
    return response("Successfully Stop SSALGO Strategy")


@native_legacy_router.post("/api_start_ssalgo", response_model=ApiResponse)
async def api_start_ssalgo(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one(
        {"botcode": botcode, "user": current_username(user)},
        fractal_reset_update(botcode, current_username(user), {"status": "opened"}),
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return response("Successfully started SSALGO strategy")


@native_legacy_router.post("/api_stop_admin_ssalgo", response_model=ApiResponse)
async def api_stop_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one({"botcode": botcode}, {"$set": {"status": "paused"}})
    mark_strategy_positions_exit(botcode)
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return response("Successfully stopped SSALGO strategy")


@native_legacy_router.post("/api_start_admin_ssalgo", response_model=ApiResponse)
async def api_start_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one(
        {"botcode": botcode},
        fractal_reset_update(botcode, None, {"status": "opened"}),
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return response("Successfully started SSALGO strategy")


async def update_admin_control(request: Request, field, value, message, _admin):
    payload = await payload_from_request(request)
    symbol = form_value(payload, "symbol")
    if not symbol:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="symbol is required")
    collection("admincontrol").update_one({"symbol": symbol}, {"$set": {field: value}})
    return response(message)


@native_legacy_router.post("/api_start_control", response_model=ApiResponse)
async def api_start_control(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "controlmode", True, "Successfully started control.", _admin)


@native_legacy_router.post("/api_stop_control", response_model=ApiResponse)
async def api_stop_control(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "controlmode", False, "Successfully stopped control.", _admin)


@native_legacy_router.post("/api_start_cebuy", response_model=ApiResponse)
async def api_start_cebuy(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Buytrade", True, "Successfully triggered CE buy.", _admin)


@native_legacy_router.post("/api_start_cesell", response_model=ApiResponse)
async def api_start_cesell(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Buytrade", False, "Successfully triggered CE sell.", _admin)


@native_legacy_router.post("/api_start_pebuy", response_model=ApiResponse)
async def api_start_pebuy(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Selltrade", True, "Successfully triggered PE buy.", _admin)


@native_legacy_router.post("/api_start_pesell", response_model=ApiResponse)
async def api_start_pesell(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Selltrade", False, "Successfully triggered PE sell.", _admin)


@native_legacy_router.post("/api_start_strategyco", response_model=ApiResponse)
async def api_start_strategyco(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    existing = collection("strategyinput").find_one({"strategy": strategy})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    collection("strategyinput").update_one({"strategy": strategy}, {"$set": {"update": True}})
    return response("Successfully started the strategy.")


@native_legacy_router.post("/api_stop_strategyco", response_model=ApiResponse)
async def api_stop_strategyco(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    existing = collection("strategyinput").find_one({"strategy": strategy})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    collection("strategyinput").update_one({"strategy": strategy}, {"$set": {"update": False}})
    return response("Successfully stopped the strategy.")


@native_legacy_router.post("/api_delete_admin_ssalgo", response_model=ApiResponse)
async def api_delete_admin_ssalgo(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    botcode = form_value(payload, "id") or form_value(payload, "botcode")
    result = collection("strategies").update_one({"botcode": botcode}, {"$set": {"status": "closed"}})
    mark_strategy_positions_exit(botcode)
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found.")
    return response("Successfully closed the strategy.")


@native_legacy_router.post("/api_delete_strategy", response_model=ApiResponse)
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
    return response("Successfully closed the strategy.")


@native_legacy_router.post("/api_forgot_reset_password", response_model=ApiResponse)
async def api_forgot_reset_password(request: Request):
    payload = await payload_from_request(request)
    email = str(form_value(payload, "email")).lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required.")
    user = collection("users").find_one({"email": email})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user found with that email address.")
    reset_token = user.get("reset_token") or secrets.token_urlsafe(32)
    collection("users").update_one({"_id": user["_id"]}, {"$set": {"reset_token": reset_token}})
    return response("Password reset token created.", {"reset_token": reset_token})


@native_legacy_router.api_route("/api_reset_password/{reset_token}", methods=["GET", "POST"], response_model=ApiResponse)
async def api_reset_password(reset_token: str, request: Request):
    user = collection("users").find_one({"reset_token": reset_token})
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token.")
    if request.method == "GET":
        return response("Reset token is valid. You can now reset your password.")
    payload = await payload_from_request(request)
    new_password = form_value(payload, "new_password")
    confirm_password = form_value(payload, "confirm_password")
    if not new_password or new_password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")
    import bcrypt

    collection("users").update_one(
        {"_id": user["_id"]},
        {"$set": {"password": bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()), "reset_token": None}},
    )
    return response("Your password has been successfully reset. You can now log in with your new password.")


@native_legacy_router.post("/api_forgot_otp_reset_password", response_model=ApiResponse)
async def api_forgot_otp_reset_password(request: Request):
    payload = await payload_from_request(request)
    email = str(form_value(payload, "email")).lower()
    user = collection("users").find_one({"email": email})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user found with that email address.")
    otp = secrets.randbelow(900000) + 100000
    expiration = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    collection("users").update_one({"_id": user["_id"]}, {"$set": {"otp": otp, "otp_expiration": expiration}})
    return response("OTP created.", {"otp": otp, "expires_at": expiration.isoformat()})


def verify_otp(email, otp):
    user = collection("users").find_one({"email": str(email).lower()})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user found with that email address.")
    if str(user.get("otp")) != str(otp):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP.")
    if user.get("otp_expiration") and user["otp_expiration"] < datetime.datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP expired.")
    return user


@native_legacy_router.post("/api_otp_verify", response_model=ApiResponse)
async def api_otp_verify(request: Request):
    payload = await payload_from_request(request)
    verify_otp(form_value(payload, "email"), form_value(payload, "otp"))
    return response("Your OTP has been successfully Matched.")


@native_legacy_router.post("/api_otp_reset_password", response_model=ApiResponse)
async def api_otp_reset_password(request: Request):
    payload = await payload_from_request(request)
    user = verify_otp(form_value(payload, "email"), form_value(payload, "otp"))
    new_password = form_value(payload, "new_password")
    confirm_password = form_value(payload, "confirm_password")
    if not new_password or new_password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")
    import bcrypt

    collection("users").update_one(
        {"_id": user["_id"]},
        {"$set": {"password": bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()), "otp": None, "otp_expiration": None}},
    )
    return response("Your password has been successfully reset. You can now log in with your new password.")

