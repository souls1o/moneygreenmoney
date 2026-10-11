from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ReadPreference
from pymongo.errors import PyMongoError

from casino.config.settings import Settings
from casino.logging.logger import get_logger

COLLECTION_USERS = "users"
COLLECTION_GAMES = "games"
COLLECTION_TRANSACTIONS = "transactions"
COLLECTION_LEDGER = "ledger"
COLLECTION_HOUSE = "house"
COLLECTION_CURSORS = "cursors"
COLLECTION_LOCKS = "locks"
COLLECTION_RATE_LIMITS = "rate_limits"
COLLECTION_GUILD_CONFIGS = "guild_configs"
COLLECTION_RAINS = "rains"


class MongoDatabase:
    """Pooled Motor client with reconnect-friendly lifecycle management."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncIOMotorClient | None = None
        self._supports_transactions = False
        self._logger = get_logger("casino.db")

    @property
    def client(self) -> AsyncIOMotorClient:
        if self._client is None:
            raise RuntimeError("MongoDB client is not connected.")
        return self._client

    @property
    def db(self) -> AsyncIOMotorDatabase:
        return self.client[self._settings.mongodb_database]

    @property
    def supports_transactions(self) -> bool:
        return self._supports_transactions

    async def connect(self) -> None:
        self._client = AsyncIOMotorClient(
            self._settings.mongodb_uri.get_secret_value(),
            uuidRepresentation="standard",
            retryWrites=True,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            maxPoolSize=50,
            minPoolSize=2,
        )
        hello = await self.client.admin.command("hello", read_preference=ReadPreference.PRIMARY)
        self._supports_transactions = bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"
        self._logger.info(
            "mongodb_connected",
            database=self._settings.mongodb_database,
            transactions=self._supports_transactions,
        )
        if not self._supports_transactions:
            self._logger.warning(
                "mongodb_standalone_mode",
                detail="Multi-document transactions need a replica set or Atlas. Using sequential writes.",
            )

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._logger.info("mongodb_disconnected")

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except PyMongoError:
            self._logger.exception("mongodb_ping_failed")
            return False
