from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameType
from casino.services.user_service import UserService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.rng import ProvablyFairService
from tests.conftest import FrozenClock, StubSettings
from tests.fakes import MemoryUserRepository
from tests.test_game_service import GameHarness


@pytest.mark.asyncio
async def test_vs_house_uses_precommitted_account_hash() -> None:
    harness = GameHarness()
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    harness.seed_user("1", "10")
    before = await harness.service._user_service.ensure_fairness("1")
    assert before.next_server_seed_hash
    assert before.fair_client_seed
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.LIMBO,
        limbo_target=Decimal("2"),
    )
    after = await harness.service._user_service.ensure_fairness("1")
    assert game.server_seed_hash == before.next_server_seed_hash
    assert game.client_seed == before.fair_client_seed
    assert game.nonce == before.fair_nonce
    assert after.fair_nonce != before.fair_nonce


@pytest.mark.asyncio
async def test_randomize_client_seed_keeps_next_hash() -> None:
    users = UserService(
        MemoryUserRepository(),
        IdentifierFactory(),
        FrozenClock(),
        StubSettings(),  # type: ignore[arg-type]
        rng=ProvablyFairService(),
    )
    first = await users.ensure_fairness("1")
    second = await users.randomize_client_seed("1")
    assert second.fair_client_seed != first.fair_client_seed
    assert second.next_server_seed_hash == first.next_server_seed_hash
    assert second.fair_nonce == first.fair_nonce
