import argparse
import json

import pymongo

from connectors.connector import AliceBlueTradeHubAdapter


def mask_value(value):
    if not value:
        return None
    return f"<set:{len(str(value))}>"


def build_result(user, broker, api):
    return {
        "user": user,
        "selectedbroker": broker.get("selectedbroker") if broker else None,
        "credentials": {
            "apikey": mask_value(api.get("apikey")) if api else None,
            "auth_code": mask_value(api.get("auth_code")) if api else None,
            "apisecret": mask_value(api.get("apisecret")) if api else None,
        },
        "session_ok": False,
        "response_keys": [],
        "error": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Test AliceBlue Ant-A3 login without placing orders.")
    parser.add_argument("--user", default="kinguniverse129", help="Application username to test.")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017", help="MongoDB URI.")
    parser.add_argument("--db", default="demo", help="MongoDB database name.")
    parser.add_argument("--alice-user-id", help="AliceBlue user/client ID. Overrides Mongo apikey.")
    parser.add_argument("--app-key", help="AliceBlue App Key. Overrides Mongo auth_code.")
    parser.add_argument("--app-secret", help="AliceBlue App Secret Key. Overrides Mongo apisecret.")
    args = parser.parse_args()

    client = pymongo.MongoClient(args.mongo_uri, serverSelectionTimeoutMS=3000)
    db = client[args.db]
    broker = db["broker"].find_one({"user": args.user}, {"_id": 0})
    api = db["apis"].find_one({"user": args.user, "broker": "aliceblue"}, {"_id": 0})
    api = dict(api or {})
    if args.alice_user_id:
        api["apikey"] = args.alice_user_id
    if args.app_key:
        api["auth_code"] = args.app_key
    if args.app_secret:
        api["apisecret"] = args.app_secret

    result = build_result(args.user, broker, api)

    missing = [
        field
        for field in ("apikey", "auth_code", "apisecret")
        if not api or not str(api.get(field, "")).strip()
    ]
    if missing:
        result["error"] = f"Missing AliceBlue field(s): {', '.join(missing)}"
        print(json.dumps(result, indent=2))
        return 1

    try:
        alice = AliceBlueTradeHubAdapter(
            user_id=str(api["apikey"]).strip(),
            auth_code=str(api["auth_code"]).strip(),
            secret_key=str(api["apisecret"]).strip(),
        )
        session = alice.get_session_id()
        result["response_keys"] = sorted(session.keys()) if isinstance(session, dict) else [type(session).__name__]
        result["session_ok"] = isinstance(session, dict) and bool(session.get("userSession"))
        if not result["session_ok"]:
            if isinstance(session, dict):
                result["error"] = session.get("emsg") or session.get("message") or session.get("stat") or session.get("status")
            else:
                result["error"] = f"Unexpected response type: {type(session).__name__}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, indent=2))
    return 0 if result["session_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
