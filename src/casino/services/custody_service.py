from __future__ import annotations

from casino.domain.records import GameRecord, JackpotRecord, is_house_seat
from casino.logging.logger import get_logger
from casino.solana_client.rpc import SolanaRpcGateway
from casino.utils.money import SolAmount


class CustodyService:
    """Move SOL between player deposit wallets and the house wallet to match game P&L."""

    def __init__(self, rpc: SolanaRpcGateway | None = None) -> None:
        self._rpc = rpc
        self._logger = get_logger("casino.custody")

    def is_live(self) -> bool:
        return self._rpc is not None and self._rpc.is_configured

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
