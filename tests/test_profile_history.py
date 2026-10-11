from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType
from casino.domain.records import GameRecord
from casino.utils.money import MathService
from tests.conftest import StubSettings
from tests.test_game_service import GameHarness


class _Container:
    math = MathService(300)
    settings = StubSettings()


def _completed(
    *,
    game_id: str,
    winners: list[str],
    game_type: GameType = GameType.COINFLIP,
    mode: GameMode = GameMode.SOLO,
    max_players: int = 2,
    heads: list[str],
    tails: list[str],
    resolved_at: datetime,
    fee: Decimal = Decimal("0.30"),
) -> GameRecord:
    return GameRecord(
        game_id=game_id,
        status=GameStatus.COMPLETED,
        creator_id=heads[0] if heads else tails[0],
        bet_amount_lamports=50_000_000,
        currency="USD",
        bet_amount_fiat=Decimal("5"),
        game_type=game_type,
        mode=mode,
        heads_ids=heads,
        tails_ids=tails,
        winner_ids=winners,
        winner_id=winners[0],
        house_fee_usd=fee,
        max_players=max_players,
        resolved_at=resolved_at,
    )


def test_player_profit_is_payout_minus_stake_after_fee() -> None:
    game = _completed(
        game_id="g1",
        winners=["1"],
        heads=["1"],
        tails=["2"],
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert game.player_profit_fiat("1") == Decimal("4.70")
    assert game.player_profit_fiat("2") == Decimal("-5")


def test_history_line_format() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    win = _completed(
        game_id="g1",
        winners=["1"],
        heads=["1"],
        tails=["2"],
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    loss = _completed(
        game_id="g2",
        winners=["2"],
        game_type=GameType.DICE,
        max_players=3,
        heads=["1", "3"],
        tails=[],
        resolved_at=datetime(2026, 1, 2, tzinfo=UTC),
        fee=Decimal("0.45"),
    )
    team = _completed(
        game_id="g3",
        winners=["1", "3"],
        mode=GameMode.TEAM,
        max_players=4,
        heads=["1", "3"],
        tails=["2", "4"],
        resolved_at=datetime(2026, 1, 3, tzinfo=UTC),
        fee=Decimal("0.60"),
    )
    assert factory._history_line(win, "1", 1) == "1. ✅ Coinflip 1v1 +$4.70"
    assert factory._history_line(win, "2", 1) == "1. ❌ Coinflip 1v1 -$5.00"
    assert factory._history_line(loss, "1", 2) == "2. ❌ Dice 1v1v1 -$5.00"
    assert factory._history_line(team, "1", 3) == "3. ✅ Coinflip 2v2 +$4.70"


@pytest.mark.asyncio
async def test_history_is_completed_only_and_numbers_from_the_bottom() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "30")
    harness.seed_user("2", "30")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(3):
        harness.clock._moment = first + timedelta(minutes=index)
        game = await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("5"),
            side=CoinSide.HEADS,
        )
        await harness.service.join_game(game.game_id, "2")
        await harness.ready_all(game.game_id)
    cancelled = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.cancel_game(cancelled.game_id, "1")
    games, total = await harness.service.history_for("1", page=0, limit=10)
    assert total == 3
    assert all(game.status is GameStatus.COMPLETED for game in games)
    assert cancelled.game_id not in {game.game_id for game in games}
    assert games[0].resolved_at >= games[1].resolved_at >= games[2].resolved_at
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    lines = [factory._history_line(game, "1", total - index) for index, game in enumerate(games)]
    assert lines[0].startswith("3. ✅")
    assert lines[-1].startswith("1. ✅")


@pytest.mark.asyncio
async def test_cancel_does_not_lock_wager_stats() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    await harness.service.cancel_game(game.game_id, "1")
    user = await harness.users_repo.find_by_discord_id("1")
    assert user is not None
    assert user.total_wagered_lamports == 0
    assert user.total_wagered_usd == Decimal("0")
    assert user.games_played == 0


@pytest.mark.asyncio
async def test_profile_stats_come_from_completed_games_not_stale_user_totals() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "30")
    harness.seed_user("2", "30")
    first = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(2):
        harness.clock._moment = first + timedelta(minutes=index)
        game = await harness.service.create_game(
            player1_id="1",
            fiat_amount=Decimal("5"),
            side=CoinSide.HEADS,
        )
        await harness.service.join_game(game.game_id, "2")
        await harness.ready_all(game.game_id)
    user = await harness.users_repo.find_by_discord_id("1")
    assert user is not None
    user.total_won_usd = Decimal("0")
    user.total_wagered_usd = Decimal("15")
    user.total_won_lamports = 0
    stats = await harness.service.profile_stats("1")
    assert stats.games_played == 2
    assert stats.games_won == 2
    assert stats.wagered_usd == Decimal("10")
    assert stats.profit_usd == Decimal("9.40")
    assert stats.wagered_lamports == 100_000_000
    assert stats.profit_lamports > 0
    assert stats.favorite_game == "Coinflip"
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]

    class _Member:
        display_name = "Alice"
        display_avatar = type("Avatar", (), {"url": "https://example.com/a.png"})()

    panel = factory.profile(_Member(), user, stats)  # type: ignore[arg-type]
    text = _panel_text(panel)
    assert "$10.00" in text
    assert "+$9.40" in text
    assert "-$10.00" not in text
    assert "-$15.00" not in text


def test_history_footer_shows_page_number() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    games = [
        _completed(
            game_id=f"g{index}",
            winners=["1"],
            heads=["1"],
            tails=["2"],
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index in range(10)
    ]
    first = factory.history("1", games, 0, total=11, page_size=10)
    second = factory.history("1", games[:1], 1, total=11, page_size=10)
    empty = factory.history("1", [], 0, total=0, page_size=10)
    assert "Page 1 of 2" in _panel_text(first)
    assert "Page 2 of 2" in _panel_text(second)
    assert "Page 1 of 1" in _panel_text(empty)


def test_joined_table_includes_private_channel_link() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    game = _completed(
        game_id="g1",
        winners=["1"],
        heads=["1"],
        tails=["2"],
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    game.status = GameStatus.PENDING
    game.private_channel_id = "12345"
    text = _panel_text(factory.joined_table(game))
    assert "<#12345>" in text
    assert "You joined" in text


def test_table_overview_uses_provably_fair_button_not_a_field() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    game = _completed(
        game_id="g1",
        winners=["1"],
        heads=["1"],
        tails=["2"],
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    game.status = GameStatus.PENDING
    game.server_seed_hash = "abc123"
    game.client_seed = "client"
    panel = factory.table_overview(game)
    text = _panel_text(panel)
    labels = [
        getattr(item, "label", None)
        for item in _walk_items(panel.view)
    ]
    assert "Verify" in labels
    assert "Hash `" not in text
    assert "abc123" not in text
    fair = factory.provably_fair(game)
    fair_text = _panel_text(fair)
    assert "abc123" in fair_text
    assert "How to use the verifier" in fair_text


def test_table_overview_builds_for_non_usd_currency() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    game = _completed(
        game_id="g1",
        winners=["1"],
        heads=["1"],
        tails=["2"],
        resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    game.status = GameStatus.PENDING
    game.winner_ids = []
    game.winner_id = None
    game.currency = "INR"
    game.bet_amount_fiat = Decimal("1000")
    game.bet_amount_usd = Decimal("10.33")
    panel = factory.table_overview(game)
    text = _panel_text(panel)
    assert "₹1,000.00" in text
    assert "$10.33" in text


def _walk_items(view) -> list[object]:
    items: list[object] = []
    seen: set[int] = set()
    stack = [view]
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        items.append(current)
        children = getattr(current, "children", None) or getattr(current, "_children", None) or ()
        stack.extend(list(children))
        walked = getattr(current, "walk_children", None)
        if callable(walked):
            stack.extend(list(walked()))
    return items


def test_howto_covers_games_fees_and_affiliate() -> None:
    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    text = _panel_text(factory.howto())
    assert "How to play" in text
    assert "/coinflip" in text
    assert "/dice" in text
    assert "3% of the total pot" in text
    assert "$0.01" in text
    assert "/affiliate" in text
    assert "5–10%" in text or "5-10%" in text


def _panel_text(panel) -> str:
    chunks: list[str] = []
    for item in _walk_items(panel.view):
        content = getattr(item, "content", None)
        if content:
            chunks.append(str(content))
    return "\n".join(chunks)
