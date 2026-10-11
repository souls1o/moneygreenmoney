from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType, LedgerEntryType
from casino.domain.records import HOUSE_USER_ID, UserRecord, is_house_seat
from casino.errors import GameError, InsufficientBalanceError, ValidationError
from casino.services.balance_service import BalanceService
from casino.services.game_service import GameService
from casino.services.ledger_service import LedgerService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount
from tests.conftest import FixedRandomService, FrozenClock, StubPriceService, StubSettings
from tests.fakes import (
    MemoryGameRepository,
    MemoryHouseRepository,
    MemoryLedgerRepository,
    MemoryTransactionRepository,
    MemoryUserRepository,
    NullUnitOfWorkFactory,
)


class GameHarness:
    def __init__(self, side: CoinSide = CoinSide.HEADS) -> None:
        self.clock = FrozenClock()
        self.ids = IdentifierFactory()
        self.math = MathService(300)
        self.users_repo = MemoryUserRepository()
        self.games_repo = MemoryGameRepository()
        self.ledger_repo = MemoryLedgerRepository()
        self.tx_repo = MemoryTransactionRepository()
        self.house_repo = MemoryHouseRepository()
        ledger = LedgerService(self.ledger_repo, self.ids, self.clock)
        balances = BalanceService(
            self.users_repo, self.tx_repo, self.house_repo, ledger, self.ids, self.clock
        )
        settings = StubSettings()
        users = UserService(self.users_repo, self.ids, self.clock, settings)  # type: ignore[arg-type]
        self.service = GameService(
            self.games_repo,
            self.users_repo,
            users,
            balances,
            StubPriceService(Decimal("100")),
            ValidationService(self.math, Decimal("0.01"), ("USD", "EUR")),
            self.math,
            FixedRandomService(side),
            self.ids,
            self.clock,
            NullUnitOfWorkFactory(),
            settings,  # type: ignore[arg-type]
            self.house_repo,
        )

    def seed_user(self, discord_id: str, sol: str = "10", currency: str = "USD") -> UserRecord:
        user = UserRecord(
            discord_id=discord_id,
            sol_balance_lamports=SolAmount.parse(sol).lamports,
            deposit_memo=self.ids.deposit_memo(),
            display_currency=currency,
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        self.users_repo.users[discord_id] = user
        return user

    async def ready_all(self, game_id: str):
        game = await self.service.get(game_id)
        last = game
        for player_id in game.human_player_ids():
            if not last.is_ready(player_id):
                last = await self.service.mark_ready(game_id, player_id)
        if last.is_awaiting_settlement():
            last = await self.service.finalize_coinflip(game_id)
        return last


@pytest.mark.asyncio
async def test_create_join_applies_three_percent_fee_and_credits_winner() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")

    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    joined = await harness.service.join_game(game.game_id, "2")
    assert joined.status is GameStatus.PENDING
    resolved = await harness.ready_all(game.game_id)

    assert resolved.status is GameStatus.COMPLETED
    assert resolved.winner_id == "1"
    assert resolved.house_fee_lamports == 3_000_000
    assert resolved.winner_payout_lamports == 97_000_000

    p1 = await harness.users_repo.find_by_discord_id("1")
    p2 = await harness.users_repo.find_by_discord_id("2")
    assert p1 is not None and p2 is not None
    assert p1.sol_balance_lamports == 10_000_000_000 - 50_000_000 + 97_000_000
    assert p2.sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert p1.total_wagered_usd == Decimal("5")
    assert p2.total_wagered_usd == Decimal("5")
    assert p1.total_wagered_lamports == 50_000_000
    assert p2.total_wagered_lamports == 50_000_000
    assert p1.total_won_usd == Decimal("9.70")
    assert p2.total_won_usd == Decimal("0")
    assert p1.total_won_usd - p1.total_wagered_usd == Decimal("4.70")
    assert p2.total_won_usd - p2.total_wagered_usd == Decimal("-5")
    assert harness.house_repo.record.sol_balance_lamports == 3_000_000


@pytest.mark.asyncio
async def test_create_game_rejects_below_one_cent_usd() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    with pytest.raises(ValidationError, match=r"Minimum bet is \$0\.01"):
        await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("0.009"),
            side=CoinSide.HEADS,
        )


@pytest.mark.asyncio
async def test_create_game_allows_one_cent_usd() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("0.01"),
        side=CoinSide.HEADS,
    )
    assert game.bet_amount_fiat == Decimal("0.01")


@pytest.mark.asyncio
async def test_create_game_has_no_player_vs_player_max() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "250")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("20000"),
        side=CoinSide.HEADS,
    )
    assert game.bet_amount_fiat == Decimal("20000")


@pytest.mark.asyncio
async def test_new_games_use_random_client_seed_and_incrementing_nonce() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    first = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    second = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    assert first.nonce == 1
    assert second.nonce == 2
    assert first.client_seed == "fixed-client-seed"
    assert first.client_seed != first.game_id
    assert first.server_seed_hash == "fixed-hash"


@pytest.mark.asyncio
async def test_history_numbers_increment_per_game_type() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "20")
    harness.seed_user("2", "20")
    first_flip = await _finish_home_coinflip(harness)
    first_dice = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        guild_id="99",
    )
    second_flip = await _finish_home_coinflip(harness)
    assert first_flip.display_title() == "Coinflip #1"
    assert first_dice.display_title() == "Dice"
    assert first_dice.game_number is None
    assert second_flip.display_title() == "Coinflip #2"


@pytest.mark.asyncio
async def test_resolve_keeps_the_original_client_seed() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.client_seed == "fixed-client-seed"
    assert resolved.nonce == game.nonce


@pytest.mark.asyncio
async def test_ledger_entries_for_a_resolved_game_sum_to_zero() -> None:
    harness = GameHarness(CoinSide.TAILS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    await harness.ready_all(game.game_id)

    entries = await harness.ledger_repo.list_for_game(game.game_id)
    types = {entry.type for entry in entries}
    assert LedgerEntryType.BET in types
    assert LedgerEntryType.WIN in types
    assert LedgerEntryType.FEE in types
    assert sum(entry.amount_lamports for entry in entries) == 0
    assert any(entry.user_id == HOUSE_USER_ID for entry in entries)
    for entry in entries:
        if entry.user_id != HOUSE_USER_ID:
            user = await harness.users_repo.find_by_discord_id(entry.user_id)
            assert user is not None
            if entry.timestamp == entries[-1].timestamp or entry.type is LedgerEntryType.WIN:
                pass


@pytest.mark.asyncio
async def test_concurrent_joins_allow_only_one_player() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.seed_user("3", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)

    results = await asyncio.gather(
        harness.service.join_game(game.game_id, "2"),
        harness.service.join_game(game.game_id, "3"),
        return_exceptions=True,
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], GameError)

    stored = await harness.games_repo.find_by_id(game.game_id)
    assert stored is not None
    assert stored.player2_id in {"2", "3"}
    leftover = "3" if stored.player2_id == "2" else "2"
    leftover_user = await harness.users_repo.find_by_discord_id(leftover)
    assert leftover_user is not None
    assert leftover_user.sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_team_game_splits_payout_between_winners() -> None:
    harness = GameHarness(CoinSide.HEADS)
    for player_id in ("1", "2", "3", "4"):
        harness.seed_user(player_id, "10")

    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        mode=GameMode.TEAM,
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    await harness.service.join_game(game.game_id, "3", CoinSide.HEADS)
    seated = await harness.service.join_game(game.game_id, "4", CoinSide.TAILS)
    assert seated.status is GameStatus.PENDING
    resolved = await harness.ready_all(game.game_id)

    assert resolved.status is GameStatus.COMPLETED
    assert set(resolved.winner_ids) == {"1", "3"}
    assert resolved.house_fee_lamports == 6_000_000
    assert resolved.winner_payout_lamports == 194_000_000
    assert resolved.per_winner_payout_lamports == 97_000_000

    p1 = await harness.users_repo.find_by_discord_id("1")
    p3 = await harness.users_repo.find_by_discord_id("3")
    p2 = await harness.users_repo.find_by_discord_id("2")
    assert p1 is not None and p3 is not None and p2 is not None
    assert p1.sol_balance_lamports == 10_000_000_000 - 50_000_000 + 97_000_000
    assert p3.sol_balance_lamports == 10_000_000_000 - 50_000_000 + 97_000_000
    assert p2.sol_balance_lamports == 10_000_000_000 - 50_000_000


@pytest.mark.asyncio
async def test_call_bot_uses_house_reserves_and_returns_bet_plus_profit_on_win() -> None:
    harness = GameHarness(CoinSide.TAILS)
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 1_000_000_000

    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    seated = await harness.service.call_bot(game.game_id, "1")
    assert seated.status is GameStatus.PENDING
    assert seated.has_player(HOUSE_USER_ID)
    resolved = await harness.ready_all(game.game_id)

    assert resolved.status is GameStatus.COMPLETED
    assert resolved.winner_ids == ["HOUSE"]
    player = await harness.users_repo.find_by_discord_id("1")
    assert player is not None
    assert player.sol_balance_lamports == 10_000_000_000 - 50_000_000
    # House: start 1 SOL, debit 0.05, return the full 0.10 pot. No fee on a house-only win.
    assert harness.house_repo.record.sol_balance_lamports == 1_050_000_000
    assert harness.house_repo.record.total_fees_collected_lamports == 0
    assert resolved.house_fee_lamports == 0
    assert resolved.per_winner_payout_lamports == 97_000_000


@pytest.mark.asyncio
async def test_team_call_bot_can_fill_seats_after_a_player_joins() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        mode=GameMode.TEAM,
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    first = await harness.service.call_bot(game.game_id, "1")
    assert first.house_seat_count() == 1
    assert first.status is GameStatus.PENDING
    second = await harness.service.call_bot(game.game_id, "1")
    assert second.house_seat_count() == 2
    assert second.is_full()
    assert second.status is GameStatus.PENDING
    assert any(is_house_seat(player_id) for player_id in second.player_ids())


@pytest.mark.asyncio
async def test_call_bot_allows_two_500_seats_then_blocks_the_third() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "20")
    harness.house_repo.record.sol_balance_lamports = 20_000_000_000
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("500"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        max_players=4,
        first_to=1,
    )
    first = await harness.service.call_bot(game.game_id, "1")
    assert first.house_seat_count() == 1
    second = await harness.service.call_bot(game.game_id, "1")
    assert second.house_seat_count() == 2
    with pytest.raises(GameError, match=r"at most \$1,000\.00"):
        await harness.service.call_bot(game.game_id, "1")
    leftover = await harness.service.get(game.game_id)
    assert leftover.house_seat_count() == 2
    assert not leftover.is_full()


@pytest.mark.asyncio
async def test_call_bot_hidden_when_single_seat_exceeds_cap() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "20")
    harness.house_repo.record.sol_balance_lamports = 20_000_000_000
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("1000.01"),
        side=CoinSide.HEADS,
    )
    assert not game.can_call_house_seat(max_house_usd=Decimal("1000"), seat_usd=Decimal("1000.01"))
    with pytest.raises(GameError, match=r"at most \$1,000\.00"):
        await harness.service.call_bot(game.game_id, "1")


@pytest.mark.asyncio
async def test_leave_refunds_one_player_and_keeps_the_table() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    left = await harness.service.leave_game(game.game_id, "2")
    assert left.status is GameStatus.PENDING
    assert not left.has_player("2")
    assert left.has_player("1")
    leaver = await harness.users_repo.find_by_discord_id("2")
    assert leaver is not None
    assert leaver.sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_leave_refunds_deducted_sol_not_current_usd_price() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    creator = await harness.users_repo.find_by_discord_id("1")
    joiner = await harness.users_repo.find_by_discord_id("2")
    assert creator is not None and joiner is not None
    deducted = 10_000_000_000 - creator.sol_balance_lamports
    assert deducted == game.bet_amount_lamports
    assert joiner.sol_balance_lamports == 10_000_000_000 - deducted
    harness.service._prices.set_usd(Decimal("200"))  # type: ignore[attr-defined]
    left = await harness.service.leave_game(game.game_id, "2")
    assert left.wager_for("2").lamports == deducted
    refunded = await harness.users_repo.find_by_discord_id("2")
    assert refunded is not None
    assert refunded.sol_balance_lamports == 10_000_000_000
    cancelled = await harness.service.cancel_game(game.game_id, "1")
    assert cancelled.wager_for("1").lamports == deducted
    creator = await harness.users_repo.find_by_discord_id("1")
    assert creator is not None
    assert creator.sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_last_player_leaving_closes_the_table_and_refunds_house() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 1_000_000_000
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.attach_listing(game.game_id, "open-games", "listing-1")
    emptied = await harness.service.leave_game(game.game_id, "1")
    assert emptied.status is GameStatus.CANCELLED
    assert emptied.channel_id == "open-games"
    assert emptied.message_id == "listing-1"
    assert emptied.human_player_ids() == []
    creator = await harness.users_repo.find_by_discord_id("1")
    assert creator is not None
    assert creator.sol_balance_lamports == 10_000_000_000
    assert harness.house_repo.record.sol_balance_lamports == 1_000_000_000


@pytest.mark.asyncio
async def test_full_table_auto_flips_after_delay_without_ready() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.service._settings.auto_flip_after_seconds = 3600  # type: ignore[attr-defined]
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    seated = await harness.service.join_game(game.game_id, "2")
    assert seated.status is GameStatus.PENDING
    assert seated.auto_flip_at is not None
    none_due = await harness.service.auto_flip_due_games()
    assert none_due == []
    harness.clock.advance(3600)
    flipped = await harness.service.auto_flip_due_games()
    assert len(flipped) == 1
    assert flipped[0].is_awaiting_settlement()
    creator = await harness.users_repo.find_by_discord_id("1")
    assert creator is not None
    assert creator.sol_balance_lamports == 10_000_000_000 - 50_000_000
    settled = await harness.service.finalize_coinflip(flipped[0].game_id)
    assert settled.status is GameStatus.COMPLETED


@pytest.mark.asyncio
async def test_open_table_does_not_auto_flip_while_waiting_for_players() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    harness.clock.advance(10_000)
    assert await harness.service.auto_flip_due_games() == []
    stored = await harness.service.get(game.game_id)
    assert stored.status is GameStatus.PENDING
    assert stored.auto_flip_at is None


@pytest.mark.asyncio
async def test_cancel_refunds_every_human_at_the_table() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        mode=GameMode.TEAM,
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    cancelled = await harness.service.cancel_game(game.game_id, "1")
    assert cancelled.status is GameStatus.CANCELLED
    for player_id in ("1", "2"):
        user = await harness.users_repo.find_by_discord_id(player_id)
        assert user is not None
        assert user.sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_cancel_refunds_creator() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    cancelled = await harness.service.cancel_game(game.game_id, "1")
    assert cancelled.status is GameStatus.CANCELLED
    user = await harness.users_repo.find_by_discord_id("1")
    assert user is not None
    assert user.sol_balance_lamports == 10_000_000_000


@pytest.mark.asyncio
async def test_ready_is_required_from_every_human() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    waiting = await harness.service.mark_ready(game.game_id, "1")
    assert waiting.status is GameStatus.PENDING
    resolved = await harness.service.mark_ready(game.game_id, "2")
    assert resolved.is_awaiting_settlement()
    settled = await harness.service.finalize_coinflip(game.game_id)
    assert settled.status is GameStatus.COMPLETED


@pytest.mark.asyncio
async def test_call_bot_rejects_non_creator() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 1_000_000_000
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    with pytest.raises(GameError, match="creator"):
        await harness.service.call_bot(game.game_id, "2")


@pytest.mark.asyncio
async def test_non_player_cannot_ready() -> None:
    harness = GameHarness()
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    with pytest.raises(GameError, match="seated"):
        await harness.service.mark_ready(game.game_id, "2")


async def _finish_home_coinflip(harness: GameHarness) -> GameRecord:
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        guild_id="99",
    )
    await harness.service.join_game(game.game_id, "2")
    return await harness.ready_all(game.game_id)


async def _finished_table(harness: GameHarness) -> str:
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    harness.games_repo.games[game.game_id].private_channel_id = "chan-1"
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    harness.games_repo.games[resolved.game_id].private_channel_id = "chan-1"
    stored = await harness.service.get(resolved.game_id)
    stored.private_channel_id = "chan-1"
    harness.games_repo.games[stored.game_id].private_channel_id = "chan-1"
    return stored.game_id


@pytest.mark.asyncio
async def test_rerun_charges_initiator_and_waits_for_other_players() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    before = await harness.users_repo.find_by_discord_id("1")
    assert before is not None
    rematch = await harness.service.start_rematch(source_id, "1")
    assert rematch.status is GameStatus.PENDING
    assert rematch.rematch_of_game_id == source_id
    assert rematch.invited_ids == ["2"]
    assert rematch.has_player("1")
    assert not rematch.has_player("2")
    after = await harness.users_repo.find_by_discord_id("1")
    assert after is not None
    assert after.sol_balance_lamports == before.sol_balance_lamports - 50_000_000


@pytest.mark.asyncio
async def test_rerun_gets_the_next_history_number() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    source = await harness.service.get(source_id)
    rematch = await harness.service.start_rematch(source_id, "1")
    assert source.display_title() == "Coinflip #1"
    assert rematch.display_title() == "Coinflip"
    assert rematch.game_number is None
    assert rematch.table_number == source.table_number
    await harness.service.accept_rematch(rematch.game_id, "2")
    finished = await harness.ready_all(rematch.game_id)
    assert finished.display_title() == "Coinflip #2"


@pytest.mark.asyncio
async def test_rerun_calls_the_same_house_bots() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    harness.games_repo.games[game.game_id].private_channel_id = "chan-1"
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    harness.games_repo.games[resolved.game_id].private_channel_id = "chan-1"
    rematch = await harness.service.start_rematch(resolved.game_id, "1")
    assert rematch.house_seat_count() == 1
    assert any(is_house_seat(player_id) for player_id in rematch.tails_ids)


@pytest.mark.asyncio
async def test_rerun_calls_two_house_bots_on_the_original_side() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 5_000_000_000
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    harness.games_repo.games[game.game_id].private_channel_id = "chan-1"
    resolved = await harness.ready_all(game.game_id)
    harness.games_repo.games[resolved.game_id].private_channel_id = "chan-1"
    rematch = await harness.service.start_rematch(resolved.game_id, "1")
    assert rematch.house_seat_count() == 2
    assert sum(1 for player_id in rematch.tails_ids if is_house_seat(player_id)) == 2
    assert rematch.has_player("1")
    assert not rematch.has_player("2")


@pytest.mark.asyncio
async def test_accept_rematch_seats_the_original_side() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    rematch = await harness.service.start_rematch(source_id, "1")
    accepted = await harness.service.accept_rematch(rematch.game_id, "2")
    assert accepted.has_player("2")
    assert "2" in accepted.tails_ids
    assert not accepted.is_awaiting_rematch_accepts()


@pytest.mark.asyncio
async def test_decline_rematch_refunds_paid_players_and_cancels() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    before = await harness.users_repo.find_by_discord_id("1")
    rematch = await harness.service.start_rematch(source_id, "1")
    declined = await harness.service.decline_rematch(rematch.game_id, "2")
    assert declined.status is GameStatus.CANCELLED
    after = await harness.users_repo.find_by_discord_id("1")
    assert before is not None and after is not None
    assert after.sol_balance_lamports == before.sol_balance_lamports


@pytest.mark.asyncio
async def test_rerun_requires_enough_balance() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    harness.users_repo.users["2"].sol_balance_lamports = 1
    with pytest.raises(InsufficientBalanceError):
        await harness.service.start_rematch(source_id, "2")


@pytest.mark.asyncio
async def test_idle_completed_table_is_due_after_five_minutes() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    stored = await harness.service.get(source_id)
    assert stored.channel_delete_at is not None
    assert await harness.service.close_idle_tables() == []
    harness.clock.advance(300)
    closed = await harness.service.close_idle_tables()
    assert len(closed) == 1
    assert closed[0].game_id == source_id
    assert await harness.service.close_idle_tables() == []


@pytest.mark.asyncio
async def test_accepted_rematch_is_not_idle_deleted() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    rematch = await harness.service.start_rematch(source_id, "1")
    accepted = await harness.service.accept_rematch(rematch.game_id, "2")
    assert accepted.channel_delete_at is None
    harness.clock.advance(300)
    assert await harness.service.close_idle_tables() == []
    stored = await harness.service.get(rematch.game_id)
    assert stored.status is GameStatus.PENDING


@pytest.mark.asyncio
async def test_unaccepted_rematch_expires_and_refunds() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    source_id = await _finished_table(harness)
    before = await harness.users_repo.find_by_discord_id("1")
    rematch = await harness.service.start_rematch(source_id, "1")
    harness.clock.advance(300)
    closed = await harness.service.close_idle_tables()
    assert len(closed) == 1
    assert closed[0].game_id == rematch.game_id
    assert closed[0].status is GameStatus.CANCELLED
    after = await harness.users_repo.find_by_discord_id("1")
    assert before is not None and after is not None
    assert after.sol_balance_lamports == before.sol_balance_lamports


_SEAT = 1_000_000_000  # $100 at $100/SOL
_FEE_ON_200 = 60_000_000
_FEE_ON_400 = 120_000_000


def _assert_house_books(harness: GameHarness, *, start: int, fees: int, call_bot: int) -> None:
    house = harness.house_repo.record
    assert house.total_fees_collected_lamports == fees
    assert house.sol_balance_lamports - start == call_bot + fees


@pytest.mark.asyncio
async def test_pvp_1v1_adds_three_percent_of_the_pot_to_fees() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_200
    assert resolved.per_winner_payout_lamports == 1_940_000_000
    _assert_house_books(harness, start=start, fees=_FEE_ON_200, call_bot=0)


@pytest.mark.asyncio
async def test_1v1_house_win_credits_call_bot_not_fees() -> None:
    harness = GameHarness(CoinSide.TAILS)
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == 0
    assert resolved.per_winner_payout_lamports == 1_940_000_000
    _assert_house_books(harness, start=start, fees=0, call_bot=_SEAT)


@pytest.mark.asyncio
async def test_1v1_player_beats_house_takes_fee_and_call_bot_loss() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_200
    _assert_house_books(harness, start=start, fees=_FEE_ON_200, call_bot=-_SEAT)


@pytest.mark.asyncio
async def test_2v2_one_house_seat_win_fees_teammate_and_takes_call_bot() -> None:
    harness = GameHarness(CoinSide.TAILS)
    for player_id in ("1", "2", "3"):
        harness.seed_user(player_id, "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    await harness.service.join_game(game.game_id, "3", CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_200
    _assert_house_books(harness, start=start, fees=_FEE_ON_200, call_bot=_SEAT)


@pytest.mark.asyncio
async def test_2v2_one_house_seat_loss_fees_both_winners() -> None:
    harness = GameHarness(CoinSide.HEADS)
    for player_id in ("1", "2", "3"):
        harness.seed_user(player_id, "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    await harness.service.join_game(game.game_id, "3", CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_400
    _assert_house_books(harness, start=start, fees=_FEE_ON_400, call_bot=-_SEAT)


@pytest.mark.asyncio
async def test_2v2_three_house_seats_player_team_win() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_200
    _assert_house_books(harness, start=start, fees=_FEE_ON_200, call_bot=-_SEAT)


@pytest.mark.asyncio
async def test_2v2_two_house_seats_vs_two_players_house_win() -> None:
    harness = GameHarness(CoinSide.TAILS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == 0
    _assert_house_books(harness, start=start, fees=0, call_bot=2 * _SEAT)


@pytest.mark.asyncio
async def test_2v2_two_house_seats_vs_two_players_player_win() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.HEADS)
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_400
    _assert_house_books(harness, start=start, fees=_FEE_ON_400, call_bot=-2 * _SEAT)


@pytest.mark.asyncio
async def test_2v2_one_house_seat_on_each_team_is_flat_call_bot() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    harness.house_repo.record.sol_balance_lamports = 5 * _SEAT
    start = harness.house_repo.record.sol_balance_lamports
    game = await harness.service.create_game(
        player1_id="1", fiat_amount=Decimal("100"), side=CoinSide.HEADS, mode=GameMode.TEAM
    )
    await harness.service.join_game(game.game_id, "2", CoinSide.TAILS)
    await harness.service.call_bot(game.game_id, "1")
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == _FEE_ON_200
    _assert_house_books(harness, start=start, fees=_FEE_ON_200, call_bot=0)


@pytest.mark.asyncio
async def test_coinflip_first_to_flips_until_a_side_wins() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.service._rng._coin_sides = [CoinSide.HEADS, CoinSide.TAILS, CoinSide.HEADS]  # type: ignore[attr-defined]
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        first_to=2,
    )
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.first_to == 2
    assert resolved.coin_flips == [CoinSide.HEADS, CoinSide.TAILS, CoinSide.HEADS]
    assert resolved.scores == {"heads": 2, "tails": 1}
    assert resolved.winner_side is CoinSide.HEADS
    assert resolved.coin_result is CoinSide.HEADS


@pytest.mark.asyncio
async def test_coinflip_first_to_three_same_side_is_three_flips() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        first_to=3,
    )
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.coin_flips == [CoinSide.HEADS, CoinSide.HEADS, CoinSide.HEADS]
    assert resolved.scores == {"heads": 3, "tails": 0}


@pytest.mark.asyncio
async def test_coinflip_does_not_credit_balances_until_finalize() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    game = await harness.service.create_game(player1_id="1", fiat_amount=Decimal("5"), side=CoinSide.HEADS)
    await harness.service.join_game(game.game_id, "2")
    revealing = game
    for player_id in ("1", "2"):
        revealing = await harness.service.mark_ready(game.game_id, player_id)
    assert revealing.is_awaiting_settlement()
    winner = await harness.users_repo.find_by_discord_id("1")
    loser = await harness.users_repo.find_by_discord_id("2")
    assert winner is not None and loser is not None
    assert winner.sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert loser.sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert winner.games_played == 0
    settled = await harness.service.finalize_coinflip(game.game_id)
    assert settled.status is GameStatus.COMPLETED
    winner = await harness.users_repo.find_by_discord_id("1")
    loser = await harness.users_repo.find_by_discord_id("2")
    assert winner is not None and loser is not None
    assert winner.sol_balance_lamports == 10_000_000_000 - 50_000_000 + 97_000_000
    assert loser.sol_balance_lamports == 10_000_000_000 - 50_000_000
    assert winner.games_played == 1
    assert winner.games_won == 1
