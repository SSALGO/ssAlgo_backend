import pymongo

from app.core.config import AppConfig


_client = None


def get_mongo_client():
    global _client
    if _client is None:
        _client = pymongo.MongoClient(AppConfig.MONGO_URI, maxPoolSize=100)
    return _client


def get_database():
    return get_mongo_client()[AppConfig.MONGO_DB]
