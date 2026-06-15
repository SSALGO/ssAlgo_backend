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
from app.core.secrets import encrypt_secret_fields
from app.domain.audit.service import AuditLogService
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
    MCXSTRATEGY_mode,
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



BACKEND_ROOT = Path(__file__).resolve().parents[3]
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
    "/api_add_mcxstrategy": ("mcxstrategy", "MCX strategy added successfully"),
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
    "/api_edit_mcxstrategy": ("mcxstrategy", "MCX strategy updated successfully"),
    "/api_edit_ssauto": ("ssauto", "SSAUTO strategy updated successfully"),
    "/api_edit_ssequity": ("ssequity_eq", "SSEQUITY strategy updated successfully"),
    "/api_edit_ssequityfno": ("ssequityfno_eq", "SSEQUITY FNO strategy updated successfully"),
    "/api_edit_sstrike": ("sstrike", "SSTRIKE strategy updated successfully"),
    "/api_edit_ema": ("ema", "EMA strategy updated successfully"),
    "/api_edit_pema": ("pema", "PEMA strategy updated successfully"),
}

ADMIN_EDIT_STRATEGY_ROUTES = {
    "/api_edit_admin_eqssalgo": ("eqssalgo", "EQSSALGO strategy updated successfully"),
    "/api_edit_admin_mcxstrategy": ("mcxstrategy", "MCX strategy updated successfully"),
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


def audit_event(event, user="", resource_type="", resource_id="", status_text="success", details=None, actor=""):
    return AuditLogService(get_database()).record(
        event,
        user=user,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status_text,
        details=details or {},
        actor=actor,
    )


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


def encrypted_secret_update(data):
    return encrypt_secret_fields(data, SECRET_FIELD_NAMES)


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
        "mcxstrategy": MCXSTRATEGY_mode,
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
        "mcxstrategy",
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
