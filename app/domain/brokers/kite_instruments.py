import csv
import datetime
import io

import requests


KITE_INSTRUMENTS_URL = "https://api.kite.trade/instruments"


def _parse_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def sync_kite_instruments(db, http=None):
    http = http or requests
    response = http.get(KITE_INSTRUMENTS_URL, timeout=30)
    response.raise_for_status()
    text = response.text
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        rows.append({
            "exchange": row.get("exchange", ""),
            "tradingsymbol": row.get("tradingsymbol", ""),
            "instrument_token": int(row.get("instrument_token") or 0),
            "instrument_type": row.get("instrument_type", ""),
            "segment": row.get("segment", ""),
            "lot_size": int(float(row.get("lot_size") or 0)),
            "tick_size": float(row.get("tick_size") or 0),
            "expiry": _parse_date(row.get("expiry")),
            "strike": float(row.get("strike") or 0),
            "name": row.get("name", ""),
            "updated_at": datetime.datetime.utcnow(),
        })

    collection = db["kite_instruments"]
    collection.create_index([("exchange", 1), ("tradingsymbol", 1)], unique=True)
    collection.create_index([("instrument_token", 1)], unique=True)
    for row in rows:
        collection.update_one(
            {"exchange": row["exchange"], "tradingsymbol": row["tradingsymbol"]},
            {"$set": row},
            upsert=True,
        )
    return {"count": len(rows), "updated_at": datetime.datetime.utcnow()}
