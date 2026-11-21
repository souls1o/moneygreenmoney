from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import GameStatus, GameType
from casino.domain.records import HOUSE_USER_ID, GameRecord
from casino.services.custody_service import CustodyService
from tests.fakes import MemoryHouseRepository
from casino.utils.money import SolAmount


class FakeRpc:
    is_configured = True
    to_house: list[tuple[str, int]] = []
    to_player: list[tuple[str, int]] = []

    async def player_to_house(self, discord_id: str, amount: SolAmount) -> str:
        self.to_house.append((discord_id, amount.lamports))
        return "loss-sig"

    async def house_to_player(self, discord_id: str, amount: SolAmount) -> str:
        self.to_player.append((discord_id, amount.lamports))
        return "win-sig"


@pytest.mark.asyncio
async def test_settle_game_sends_losses_to_house_then_wins_from_house() -> None:
    rpc = FakeRpc()
    rpc.to_house = []
    rpc.to_player = []
    custody = CustodyService(rpc)  # type: ignore[arg-type]
    game = GameRecord(
        game_id="g1",
        status=GameStatus.COMPLETED,
        creator_id="1",
        bet_amount_lamports=50_000_000,
        currency="USD",
        bet_amount_fiat=SolAmount.from_lamports(50_000_000).to_decimal() * 100,
        game_type=GameType.COINFLIP,
        heads_ids=["1"],
        tails_ids=["2"],
        winner_ids=["1"],
        winner_id="1",
        winner_payout_lamports=97_000_000,
        per_winner_payout_lamports=97_000_000,
        house_fee_lamports=3_000_000,
    )
    await custody.settle_game(game)
    assert rpc.to_house == [("2", 50_000_000)]
    assert rpc.to_player == [("1", 47_000_000)]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_sync_house_wallet_aligns_to_book() -> None:
    class AlignRpc:
        is_configured = True
        has_house_wallet = True
        aligned: list[int] = []

        async def align_house_to_book(self, book_lamports: int) -> str | None:
            self.aligned.append(book_lamports)
            return "align-sig"

    rpc = AlignRpc()
    house = MemoryHouseRepository()
    house.record.sol_balance_lamports = 3_000_000_000
    custody = CustodyService(rpc, house)  # type: ignore[arg-type]
    signature = await custody.sync_house_wallet()
    assert signature == "align-sig"
    assert rpc.aligned == [3_000_000_000]


@pytest.mark.asyncio
async def test_idle_custody_is_a_noop() -> None:
    custody = CustodyService()
    game = GameRecord(
        game_id="g1",
        status=GameStatus.COMPLETED,
        creator_id="1",
        bet_amount_lamports=50_000_000,
        currency="USD",
        bet_amount_fiat=Decimal("5"),
        heads_ids=["1"],
        tails_ids=[HOUSE_USER_ID],
        winner_ids=["1"],
        winner_id="1",
        per_winner_payout_lamports=100_000_000,
    )
    await custody.settle_game(game)
