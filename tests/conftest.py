import datetime

import pytest
from bson import ObjectId


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, matched_count=0, modified_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class FakeDeleteResult:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


class FakeCursor(list):
    def sort(self, key, direction):
        reverse = direction < 0
        return FakeCursor(sorted(self, key=lambda row: row.get(key) or datetime.datetime.min, reverse=reverse))

    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self):
        self.rows = []

    def create_index(self, *_args, **_kwargs):
        return None

    def insert_one(self, row):
        row = dict(row)
        row.setdefault("_id", ObjectId())
        self.rows.append(row)
        return FakeInsertResult(row["_id"])

    def insert_many(self, rows):
        inserted_ids = []
        for row in rows:
            inserted_ids.append(self.insert_one(row).inserted_id)
        return type("FakeInsertManyResult", (), {"inserted_ids": inserted_ids})()

    def find_one(self, query=None, *args, **kwargs):
        for row in self.find(query or {}):
            return row
        return None

    def find(self, query=None, *args, **kwargs):
        query = query or {}
        return FakeCursor([row for row in self.rows if self._matches(row, query)])

    def count_documents(self, query, limit=0):
        count = len(self.find(query))
        return min(count, limit) if limit else count

    def update_one(self, query, update, upsert=False):
        row = self.find_one(query)
        upserted_id = None
        if row is None and upsert:
            row = dict(query)
            row["_id"] = ObjectId()
            self.rows.append(row)
            upserted_id = row["_id"]
        if row is None:
            return FakeUpdateResult()

        for key, value in update.get("$setOnInsert", {}).items():
            if upserted_id is not None:
                self._set_value(row, key, value)
        for key, value in update.get("$set", {}).items():
            self._set_value(row, key, value)
        for key, value in update.get("$inc", {}).items():
            self._set_value(row, key, (self._get_value(row, key) or 0) + value)
        for key, value in update.get("$push", {}).items():
            current = self._get_value(row, key)
            if current is None:
                self._set_value(row, key, [])
                current = self._get_value(row, key)
            current.append(value)
        for key in update.get("$unset", {}).keys():
            self._unset_value(row, key)
        return FakeUpdateResult(1, 1, upserted_id)

    def update_many(self, query, update):
        matched = 0
        for row in self.find(query):
            matched += 1
            for key, value in update.get("$set", {}).items():
                row[key] = value
        return FakeUpdateResult(matched, matched)

    def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if self._matches(row, query):
                self.rows.pop(index)
                return FakeDeleteResult(1)
        return FakeDeleteResult()

    def delete_many(self, query):
        deleted = 0
        remaining = []
        for row in self.rows:
            if self._matches(row, query):
                deleted += 1
            else:
                remaining.append(row)
        self.rows = remaining
        return FakeDeleteResult(deleted)

    @staticmethod
    def _matches(row, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(FakeCollection._matches(row, item) for item in expected):
                    return False
                continue
            value = FakeCollection._get_value(row, key)
            if isinstance(expected, dict):
                if "$gte" in expected and not (value >= expected["$gte"]):
                    return False
                if "$lte" in expected and not (value <= expected["$lte"]):
                    return False
                if "$in" in expected and value not in expected["$in"]:
                    return False
                if "$nin" in expected and value in expected["$nin"]:
                    return False
                if "$ne" in expected and not (value != expected["$ne"]):
                    return False
                if "$lt" in expected and not (value < expected["$lt"]):
                    return False
                if "$exists" in expected:
                    exists = FakeCollection._has_value(row, key)
                    if bool(expected["$exists"]) != exists:
                        return False
            elif value != expected:
                return False
        return True

    @staticmethod
    def _get_value(row, dotted_key):
        current = row
        for part in str(dotted_key).split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _has_value(row, dotted_key):
        sentinel = object()
        current = row
        for part in str(dotted_key).split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return current is not sentinel

    @staticmethod
    def _set_value(row, dotted_key, value):
        parts = str(dotted_key).split(".")
        current = row
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    @staticmethod
    def _unset_value(row, dotted_key):
        parts = str(dotted_key).split(".")
        current = row
        for part in parts[:-1]:
            if not isinstance(current, dict):
                return
            current = current.get(part)
            if current is None:
                return
        if isinstance(current, dict):
            current.pop(parts[-1], None)


class FakeDatabase(dict):
    name = "fake"

    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)

    def command(self, command):
        return {"ok": 1, "command": command}


@pytest.fixture
def fake_db():
    return FakeDatabase()
