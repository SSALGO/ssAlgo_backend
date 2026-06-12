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
