from __future__ import annotations

from inspect import isawaitable
from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorDatabase

from casino.db.mongo import MongoDatabase


class MongoRepository:
    def __init__(self, mongo: MongoDatabase, collection_name: str) -> None:
        self._mongo = mongo
        self._collection_name = collection_name

    @property
    def collection(self):
        return self._mongo.db[self._collection_name]

    @property
    def database(self) -> AsyncIOMotorDatabase:
        return self._mongo.db

    async def aggregate_first(self, pipeline: list[dict], session=None) -> dict | None:
        kwargs = {}
        if session is not None:
            kwargs["session"] = session
        cursor = self.collection.aggregate(pipeline, **kwargs)
        if isawaitable(cursor):
            cursor = await cursor
        docs = await cursor.to_list(length=1)
        if not docs:
            return None
        return docs[0]

    @staticmethod
    def decimal_to_str(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def str_to_decimal(value: object, default: str = "0") -> Decimal:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value))
