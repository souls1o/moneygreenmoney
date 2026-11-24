from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import TracebackType

from casino.db.unit_of_work import MongoUnitOfWork, UnitOfWorkFactory
from casino.domain.records import GameRecord, JackpotRecord, is_house_seat
from casino.logging.logger import get_logger
from casino.repositories.house_repository import HouseRepository
from casino.solana_client.rpc import SolanaRpcGateway
from casino.utils.money import SolAmount

_house_wallet_dirty: ContextVar[bool] = ContextVar("house_wallet_dirty", default=False)


def mark_house_wallet_dirty() -> None:
    _house_wallet_dirty.set(True)


def consume_house_wallet_dirty() -> bool:
    dirty = _house_wallet_dirty.get()
    _house_wallet_dirty.set(False)
    return dirty


class CustodyService:
    """Keep house-book SOL (including investor funds) on the house wallet; players stay in super."""

    def __init__(
        self,
        rpc: SolanaRpcGateway | None = None,
        house_repository: HouseRepository | None = None,
    ) -> None:
        self._rpc = rpc
        self._house = house_repository
        self._logger = get_logger("casino.custody")
        self._sync_task: asyncio.Task[None] | None = None
        self._sync_again = False

    def is_live(self) -> bool:
        return self._rpc is not None and self._rpc.is_configured

    async def sync_house_wallet(self) -> str | None:
        if not self.is_live() or self._house is None or self._rpc is None:
            return None
        if not self._rpc.has_house_wallet:
            return None
        book = (await self._house.get()).sol_balance_lamports
        return await self._rpc.align_house_to_book(book)

    def schedule_house_wallet_sync(self) -> None:
        self._sync_again = True
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.create_task(self._drain_house_wallet_sync())

    async def _drain_house_wallet_sync(self) -> None:
        try:
            while self._sync_again:
                self._sync_again = False
                try:
                    await self.sync_house_wallet()
                except Exception:
                    self._logger.exception("house_wallet_sync_failed")
        finally:
            self._sync_task = None
            if self._sync_again:
                self.schedule_house_wallet_sync()

    async def settle_player_pnl(self, discord_id: str, profit_lamports: int) -> None:
        if not self.is_live() or profit_lamports == 0 or is_house_seat(discord_id):
            return
        amount = SolAmount.from_lamports(abs(int(profit_lamports)))
        assert self._rpc is not None
        if profit_lamports < 0:
            signature = await self._rpc.player_to_house(discord_id, amount)
            self._logger.info(
                "loss_swept_to_house",
                userId=discord_id,
                amountLamports=amount.lamports,
                txSignature=signature,
            )
            return
        signature = await self._rpc.house_to_player(discord_id, amount)
        self._logger.info(
            "win_paid_from_house",
            userId=discord_id,
            amountLamports=amount.lamports,
            txSignature=signature,
        )

    async def settle_game(self, game: GameRecord) -> None:
        if not self.is_live():
            return
        moves: list[tuple[str, int]] = []
        for player_id in game.human_player_ids():
            moves.append((player_id, game.player_profit_lamports(player_id)))
        await self._apply_pnl(moves)

    async def settle_jackpot(self, jackpot: JackpotRecord) -> None:
        if not self.is_live() or not jackpot.winner_id:
            return
        moves: list[tuple[str, int]] = []
        for player_id, stake in jackpot.entries_lamports.items():
            if player_id == jackpot.winner_id:
                moves.append((player_id, int(jackpot.winner_payout_lamports) - int(stake)))
            else:
                moves.append((player_id, -int(stake)))
        await self._apply_pnl(moves)

    async def house_to_player(self, discord_id: str, amount: SolAmount) -> None:
        if not self.is_live() or amount.is_zero() or is_house_seat(discord_id):
            return
        assert self._rpc is not None
        signature = await self._rpc.house_to_player(discord_id, amount)
        self._logger.info(
            "house_to_player",
            userId=discord_id,
            amountLamports=amount.lamports,
            txSignature=signature,
        )

    async def player_to_house(self, discord_id: str, amount: SolAmount) -> None:
        if not self.is_live() or amount.is_zero() or is_house_seat(discord_id):
            return
        assert self._rpc is not None
        signature = await self._rpc.player_to_house(discord_id, amount)
        self._logger.info(
            "player_to_house",
            userId=discord_id,
            amountLamports=amount.lamports,
            txSignature=signature,
        )

    async def player_to_player(self, source_id: str, dest_id: str, amount: SolAmount) -> None:
        if not self.is_live() or amount.is_zero() or source_id == dest_id:
            return
        assert self._rpc is not None
        signature = await self._rpc.player_to_player(source_id, dest_id, amount)
        self._logger.info(
            "player_to_player",
            sourceId=source_id,
            destId=dest_id,
            amountLamports=amount.lamports,
            txSignature=signature,
        )

    async def _apply_pnl(self, moves: list[tuple[str, int]]) -> None:
        for player_id, profit in moves:
            if profit < 0:
                await self.settle_player_pnl(player_id, profit)
        for player_id, profit in moves:
            if profit > 0:
                await self.settle_player_pnl(player_id, profit)


class HouseWalletSyncingUnitOfWorkFactory:
    """After a committed house-book change, move SOL between house and super to match `/housebal`."""

    def __init__(self, inner: UnitOfWorkFactory, custody: CustodyService) -> None:
        self._inner = inner
        self._custody = custody

    def begin(self) -> HouseWalletSyncingUnitOfWork:
        return HouseWalletSyncingUnitOfWork(self._inner.begin(), self._custody)


class HouseWalletSyncingUnitOfWork:
    def __init__(self, inner: MongoUnitOfWork, custody: CustodyService) -> None:
        self._inner = inner
        self._custody = custody

    async def __aenter__(self) -> MongoUnitOfWork:
        return await self._inner.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc, tb)
        if exc_type is None and consume_house_wallet_dirty():
            self._custody.schedule_house_wallet_sync()
        else:
            _house_wallet_dirty.set(False)
