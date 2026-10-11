from __future__ import annotations

from types import TracebackType

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.db.mongo import MongoDatabase
from casino.logging.logger import get_logger


class MongoUnitOfWork:
    """MongoDB session wrapper. Uses a multi-document transaction when the cluster supports it."""

    def __init__(self, mongo: MongoDatabase) -> None:
        self._mongo = mongo
        self.session: AsyncIOMotorClientSession | None = None
        self._in_transaction = False
        self._logger = get_logger("casino.db.uow")

    async def __aenter__(self) -> MongoUnitOfWork:
        self.session = await self._mongo.client.start_session()
        if self._mongo.supports_transactions:
            self.session.start_transaction()
            self._in_transaction = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if self._in_transaction:
                if exc_type is None:
                    await self.session.commit_transaction()
                else:
                    await self.session.abort_transaction()
                    self._logger.warning("transaction_aborted", error=str(exc))
        finally:
            await self.session.end_session()
            self.session = None
            self._in_transaction = False


class UnitOfWorkFactory:
    def __init__(self, mongo: MongoDatabase) -> None:
        self._mongo = mongo

    def begin(self) -> MongoUnitOfWork:
        return MongoUnitOfWork(self._mongo)
