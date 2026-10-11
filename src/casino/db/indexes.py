from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, IndexModel

from casino.db.mongo import (
    COLLECTION_CURSORS,
    COLLECTION_GAMES,
    COLLECTION_GUILD_CONFIGS,
    COLLECTION_HOUSE,
    COLLECTION_LEDGER,
    COLLECTION_LOCKS,
    COLLECTION_RAINS,
    COLLECTION_RATE_LIMITS,
    COLLECTION_TRANSACTIONS,
    COLLECTION_USERS,
    MongoDatabase,
)
from casino.logging.logger import get_logger


class IndexInstaller:
    def __init__(self, mongo: MongoDatabase) -> None:
        self._mongo = mongo
        self._logger = get_logger("casino.db.indexes")

    async def install(self) -> None:
        db = self._mongo.db
        await db[COLLECTION_USERS].create_indexes(
            [
                IndexModel([("discordId", ASCENDING)], unique=True),
                IndexModel([("depositMemo", ASCENDING)], unique=True),
                IndexModel([("depositAddress", ASCENDING)], unique=True, sparse=True),
                IndexModel([("walletAddress", ASCENDING)], sparse=True),
                IndexModel([("referredBy", ASCENDING)], sparse=True),
            ]
        )
        await db[COLLECTION_GAMES].create_indexes(
            [
                IndexModel([("gameId", ASCENDING)], unique=True),
                IndexModel([("status", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("status", ASCENDING), ("expiresAt", ASCENDING)]),
                IndexModel([("status", ASCENDING), ("autoFlipAt", ASCENDING)]),
                IndexModel([("status", ASCENDING), ("channelDeleteAt", ASCENDING)]),
                IndexModel([("privateChannelId", ASCENDING), ("status", ASCENDING)], sparse=True),
                IndexModel([("rematchOfGameId", ASCENDING)], sparse=True),
                IndexModel([("player1Id", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("player2Id", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("creatorId", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("headsIds", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("tailsIds", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("guildId", ASCENDING)], sparse=True),
                IndexModel([("status", ASCENDING), ("resolvedAt", DESCENDING)]),
                IndexModel(
                    [("guildId", ASCENDING), ("status", ASCENDING), ("resolvedAt", DESCENDING)],
                    sparse=True,
                ),
            ]
        )
        await db[COLLECTION_GUILD_CONFIGS].create_indexes(
            [IndexModel([("guildId", ASCENDING)], unique=True)]
        )
        await db[COLLECTION_TRANSACTIONS].update_many(
            {"solanaTxSignature": None},
            {"$unset": {"solanaTxSignature": ""}},
        )
        await db[COLLECTION_TRANSACTIONS].create_indexes(
            [
                IndexModel([("transactionId", ASCENDING)], unique=True),
                IndexModel(
                    [("solanaTxSignature", ASCENDING)],
                    unique=True,
                    sparse=True,
                    name="solanaTxSignature_1",
                ),
                IndexModel([("userId", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("type", ASCENDING), ("status", ASCENDING)]),
            ]
        )
        await db[COLLECTION_LEDGER].create_indexes(
            [
                IndexModel([("entryId", ASCENDING)], unique=True),
                IndexModel([("userId", ASCENDING), ("timestamp", DESCENDING)]),
                IndexModel([("relatedGameId", ASCENDING)], sparse=True),
                IndexModel([("relatedTxId", ASCENDING)], sparse=True),
            ]
        )
        await db[COLLECTION_HOUSE].create_indexes(
            [IndexModel([("name", ASCENDING)], unique=True)]
        )
        await db[COLLECTION_CURSORS].create_indexes(
            [IndexModel([("name", ASCENDING)], unique=True)]
        )
        await db[COLLECTION_LOCKS].create_indexes(
            [
                IndexModel([("name", ASCENDING)], unique=True),
                IndexModel([("expiresAt", ASCENDING)], expireAfterSeconds=0),
            ]
        )
        await db[COLLECTION_RATE_LIMITS].create_indexes(
            [
                IndexModel([("key", ASCENDING)], unique=True),
                IndexModel([("expiresAt", ASCENDING)], expireAfterSeconds=0),
            ]
        )
        await db[COLLECTION_RAINS].create_indexes(
            [
                IndexModel([("rainId", ASCENDING)], unique=True),
                IndexModel([("status", ASCENDING), ("expiresAt", ASCENDING)]),
                IndexModel([("status", ASCENDING), ("createdAt", DESCENDING)]),
                IndexModel([("guildId", ASCENDING), ("status", ASCENDING)], sparse=True),
                IndexModel([("channelId", ASCENDING)], sparse=True),
            ]
        )
        self._logger.info("mongodb_indexes_installed")
