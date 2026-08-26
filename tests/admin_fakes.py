"""In-memory Mongo stand-ins for the admin subscription tests.

Deliberately faithful rather than mocked: these let the tests drive the *real*
``RateLimitService`` credit math and the *real* ``SubscriptionStorage`` writes, so
a regression in the ``total_credits`` ceiling is caught here rather than in prod.
"""

import copy
from typing import Any

from pymongo.errors import DuplicateKeyError


_MISSING = object()


def _resolve(document: Any, path: str) -> Any:
    """Follow a dotted path the way Mongo does (``actor.operator``)."""
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _matches(document: dict, query: dict) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, clause) for clause in condition):
                return False
            continue

        resolved = _resolve(document, key)
        present = resolved is not _MISSING
        value = None if not present else resolved
        if isinstance(condition, dict):
            for op, operand in condition.items():
                if op == "$gt" and not (value is not None and value > operand):
                    return False
                if op == "$gte" and not (value is not None and value >= operand):
                    return False
                if op == "$lt" and not (value is not None and value < operand):
                    return False
                if op == "$lte" and not (value is not None and value <= operand):
                    return False
                if op == "$in" and value not in operand:
                    return False
                if op == "$ne" and value == operand:
                    return False
                if op == "$exists" and present != operand:
                    return False
                if op == "$type" and operand == "string" and not isinstance(value, str):
                    return False
        elif value != condition:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict]):
        self._documents = documents

    def sort(self, field, direction=1):
        self._documents = sorted(
            self._documents,
            key=lambda doc: (doc.get(field) is None, doc.get(field)),
            reverse=direction == -1,
        )
        return self

    def skip(self, count: int):
        self._documents = self._documents[count:]
        return self

    def limit(self, count: int):
        self._documents = self._documents[:count]
        return self

    async def to_list(self, length: int | None = None):
        return copy.deepcopy(self._documents[:length] if length else self._documents)


class FakeAggregation:
    def __init__(self, documents: list[dict]):
        self._documents = documents

    async def to_list(self, length: int | None = None):
        return copy.deepcopy(self._documents)


class FakeCollection:
    """Supports only the operations the code under test actually issues."""

    def __init__(self, documents: list[dict] | None = None):
        self.documents: list[dict] = [dict(doc) for doc in (documents or [])]
        self._unique_keys: list[tuple[str, ...]] = []

    async def create_index(self, keys, **kwargs):
        if kwargs.get("unique"):
            self._unique_keys.append(tuple(key for key, _ in keys))
        return "index"

    async def drop_index(self, *args, **kwargs):
        return None

    async def find_one(self, query: dict, projection: dict | None = None):
        for document in self.documents:
            if _matches(document, query):
                return copy.deepcopy(document)
        return None

    def find(self, query: dict, projection: dict | None = None):
        return FakeCursor([doc for doc in self.documents if _matches(doc, query)])

    async def count_documents(self, query: dict) -> int:
        return len([doc for doc in self.documents if _matches(doc, query)])

    async def insert_one(self, document: dict):
        for keys in self._unique_keys:
            probe = {key: document.get(key) for key in keys}
            if all(value is not None for value in probe.values()) and any(
                _matches(existing, probe) for existing in self.documents
            ):
                raise DuplicateKeyError("duplicate key")
        self.documents.append(dict(document))
        return type("InsertResult", (), {"inserted_id": document.get("_id")})()

    @staticmethod
    def _apply(document: dict, update: dict) -> None:
        for field, value in update.get("$set", {}).items():
            document[field] = value
        for field, value in update.get("$inc", {}).items():
            document[field] = (document.get(field) or 0) + value
        for field, value in update.get("$setOnInsert", {}).items():
            document.setdefault(field, value)

    async def find_one_and_update(self, query, update, return_document=True, **kwargs):
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update)
                return copy.deepcopy(document)
        return None

    async def update_one(self, query, update, upsert: bool = False):
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            document = {
                key: value
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self._apply(document, update)
            self.documents.append(document)
            return type("R", (), {"modified_count": 0, "matched_count": 0})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    async def update_many(self, query, update):
        modified = 0
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update)
                modified += 1
        return type("R", (), {"modified_count": modified})()

    def aggregate(self, pipeline: list[dict]):
        """Only the sum-over-a-match shape used by the credit-usage aggregate."""
        rows = self.documents
        for stage in pipeline:
            if "$match" in stage:
                rows = [doc for doc in rows if _matches(doc, stage["$match"])]
            elif "$group" in stage:
                field = stage["$group"]["total"]["$sum"].lstrip("$")
                total = sum(int(doc.get(field) or 0) for doc in rows)
                return FakeAggregation([{"_id": None, "total": total}])
        return FakeAggregation(rows)


class FakeDatabase(dict):
    def __missing__(self, key):
        collection = FakeCollection()
        self[key] = collection
        return collection


class FakeMongoHandler:
    def __init__(self, **collections: FakeCollection):
        self.db = FakeDatabase(collections)


class FakeTransactionService:
    """Just enough of TransactionService for the admin service to drive."""

    def __init__(self, catalog: dict, computed_view):
        self.catalog = catalog
        self._computed_view = computed_view
        self.eligibility_error: Exception | None = None

    def _get_subscription_quote(self, tier: str, period: str) -> dict:
        try:
            return dict(self.catalog[(tier, period)])
        except KeyError:
            raise ValueError("Invalid subscription tier") from None

    async def validate_subscription_eligibility(self, *, user_id, quote, now_ms=None):
        if self.eligibility_error is not None:
            raise self.eligibility_error

    async def get_user_subscription(self, user_id: str) -> dict[str, Any]:
        return await self._computed_view(user_id)
