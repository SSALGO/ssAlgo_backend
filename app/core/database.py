import os

import pymongo

from app.core.config import AppConfig


_client = None


def get_mongo_client():
    global _client
    if _client is None:
        timeout_ms = int(os.getenv("SSLAGO_MONGO_TIMEOUT_MS", "10000"))
        _client = pymongo.MongoClient(
            AppConfig.MONGO_URI,
            maxPoolSize=100,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
        )
    return _client


def get_database():
    return get_mongo_client()[AppConfig.MONGO_DB]


def ensure_core_indexes(db=None):
    db = db if db is not None else get_database()
    index_specs = {
        "Opositions": [
            ([("user", 1), ("decision", 1), ("status", 1)], "idx_opositions_user_decision_status"),
            ([("user", 1), ("botcode", 1), ("status", 1)], "idx_opositions_user_botcode_status"),
        ],
        "strategies": [
            ([("user", 1), ("status", 1)], "idx_strategies_user_status"),
            ([("user", 1), ("botcode", 1)], "idx_strategies_user_botcode"),
        ],
        "orders": [
            ([("user", 1), ("status", 1)], "idx_orders_user_status"),
        ],
        "positions": [
            ([("user", 1), ("status", 1)], "idx_positions_user_status"),
        ],
        "users": [
            ([("username", 1)], "idx_users_username"),
        ],
        "subscriptionperiod": [
            ([("user", 1)], "idx_subscriptionperiod_user"),
        ],
    }
    created = []
    for collection_name, specs in index_specs.items():
        collection = db[collection_name]
        existing_names = set(collection.index_information().keys())
        for keys, name in specs:
            if name in existing_names:
                continue
            collection.create_index(keys, name=name, background=True)
            created.append(f"{collection_name}.{name}")
    return created
