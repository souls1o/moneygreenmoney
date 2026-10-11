from __future__ import annotations

from decimal import Decimal

import pytest

from casino.domain.affiliate import affiliate_cut_lamports, affiliate_rate_bps, next_affiliate_tier
from casino.domain.enums import CoinSide, GameStatus, LedgerEntryType
from casino.errors import AffiliateError
from tests.test_game_service import GameHarness


def test_affiliate_rate_tiers() -> None:
    assert affiliate_rate_bps(Decimal("-1")) == 500
    assert affiliate_rate_bps(Decimal("0")) == 500
    assert affiliate_rate_bps(Decimal("499.99")) == 500
    assert affiliate_rate_bps(Decimal("500")) == 600
    assert affiliate_rate_bps(Decimal("1000")) == 700
    assert affiliate_rate_bps(Decimal("2500")) == 800
    assert affiliate_rate_bps(Decimal("5000")) == 900
    assert affiliate_rate_bps(Decimal("10000")) == 1000
    assert affiliate_rate_bps(Decimal("25000")) == 1000
    assert next_affiliate_tier(Decimal("0")) == (Decimal("500"), 600)
    assert next_affiliate_tier(Decimal("10000")) is None
    assert affiliate_cut_lamports(3_000_000, 500, 1) == 150_000
    assert affiliate_cut_lamports(3_000_000, 500, 2) == 75_000
    assert affiliate_cut_lamports(0, 1000, 2) == 0


@pytest.mark.asyncio
async def test_bind_rejects_self_and_cannot_be_changed() -> None:
    harness = GameHarness()
    harness.seed_user("1")
    harness.seed_user("2")
    harness.seed_user("3")

    with pytest.raises(AffiliateError, match="cannot refer yourself"):
        await harness.service._affiliates.bind("1", "1")

    bound = await harness.service._affiliates.bind("1", "2")
    assert bound.referred_by == "2"
    assert bound.referred_wagered_usd == Decimal("0")

    with pytest.raises(AffiliateError, match="cannot be changed"):
        await harness.service._affiliates.bind("1", "3")

    user = await harness.users_repo.find_by_discord_id("1")
    assert user is not None
    assert user.referred_by == "2"


@pytest.mark.asyncio
async def test_prebind_wager_starts_at_five_percent() -> None:
    harness = GameHarness(CoinSide.HEADS)
    player = harness.seed_user("1", "10")
    player.total_wagered_usd = Decimal("600")
    harness.seed_user("2", "10")
    affiliate = harness.seed_user("3", "1")
    start_affiliate = affiliate.sol_balance_lamports

    await harness.service._affiliates.bind("1", "3")
    bound = await harness.users_repo.find_by_discord_id("1")
    assert bound is not None
    assert bound.referred_wagered_usd == Decimal("0")
    assert bound.total_wagered_usd == Decimal("600")

    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.status is GameStatus.COMPLETED
    assert resolved.house_fee_lamports == 3_000_000

    player = await harness.users_repo.find_by_discord_id("1")
    paid = await harness.users_repo.find_by_discord_id("3")
    assert player is not None and paid is not None
    assert player.referred_wagered_usd == Decimal("5")
    assert player.total_wagered_usd == Decimal("605")
    assert paid.sol_balance_lamports == start_affiliate + 150_000
    assert paid.affiliate_earned_lamports == 150_000
    assert harness.house_repo.record.sol_balance_lamports == 2_850_000
    assert any(entry.type is LedgerEntryType.AFFILIATE for entry in harness.ledger_repo.entries)


@pytest.mark.asyncio
async def test_five_hundred_wagered_after_bind_pays_six_percent() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    affiliate = harness.seed_user("3", "1")
    start_affiliate = affiliate.sol_balance_lamports

    await harness.service._affiliates.bind("1", "3")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("500"),
        side=CoinSide.HEADS,
    )
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == 300_000_000

    player = await harness.users_repo.find_by_discord_id("1")
    paid = await harness.users_repo.find_by_discord_id("3")
    assert player is not None and paid is not None
    assert player.referred_wagered_usd == Decimal("500")
    assert paid.sol_balance_lamports == start_affiliate + 18_000_000
    assert harness.house_repo.record.sol_balance_lamports == 282_000_000


@pytest.mark.asyncio
async def test_house_only_win_pays_no_affiliate() -> None:
    harness = GameHarness(CoinSide.TAILS)
    harness.seed_user("1", "10")
    affiliate = harness.seed_user("3", "1")
    start_affiliate = affiliate.sol_balance_lamports
    harness.house_repo.record.sol_balance_lamports = 1_000_000_000

    await harness.service._affiliates.bind("1", "3")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.call_bot(game.game_id, "1")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.house_fee_lamports == 0

    player = await harness.users_repo.find_by_discord_id("1")
    paid = await harness.users_repo.find_by_discord_id("3")
    assert player is not None and paid is not None
    assert player.referred_wagered_usd == Decimal("5")
    assert paid.sol_balance_lamports == start_affiliate
    assert paid.affiliate_earned_lamports == 0
    assert harness.house_repo.record.sol_balance_lamports == 1_050_000_000


@pytest.mark.asyncio
async def test_referred_loser_pays_no_affiliate() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    affiliate = harness.seed_user("3", "1")
    start_affiliate = affiliate.sol_balance_lamports

    await harness.service._affiliates.bind("2", "3")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.join_game(game.game_id, "2")
    resolved = await harness.ready_all(game.game_id)
    assert resolved.winner_id == "1"
    assert resolved.house_fee_lamports == 3_000_000

    loser = await harness.users_repo.find_by_discord_id("2")
    paid = await harness.users_repo.find_by_discord_id("3")
    assert loser is not None and paid is not None
    assert loser.referred_wagered_usd == Decimal("5")
    assert paid.sol_balance_lamports == start_affiliate
    assert paid.affiliate_earned_lamports == 0
    assert harness.house_repo.record.sol_balance_lamports == 3_000_000


@pytest.mark.asyncio
async def test_two_referred_seats_only_winner_pays() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    affiliate = harness.seed_user("3", "1")
    start_affiliate = affiliate.sol_balance_lamports

    await harness.service._affiliates.bind("1", "3")
    await harness.service._affiliates.bind("2", "3")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.join_game(game.game_id, "2")
    await harness.ready_all(game.game_id)

    paid = await harness.users_repo.find_by_discord_id("3")
    assert paid is not None
    assert paid.sol_balance_lamports == start_affiliate + 150_000
    assert harness.house_repo.record.sol_balance_lamports == 2_850_000


@pytest.mark.asyncio
async def test_affiliate_stats_list_referrals() -> None:
    harness = GameHarness()
    harness.seed_user("1")
    harness.seed_user("2")
    await harness.service._affiliates.bind("2", "1")
    stats = await harness.service._affiliates.stats("1")
    assert stats.referral_count == 1
    assert stats.referrals[0].discord_id == "2"
    assert stats.referrals[0].rate_bps == 500
    own = await harness.service._affiliates.stats("2")
    assert own.referred_by == "1"
    assert own.own_rate_bps == 500
