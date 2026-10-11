from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import discord
import pytest

from casino.discord_bot.installs import is_home_guild, is_inline_game, should_number_game
from casino.discord_bot.presentation.builder import CasinoPanel
from casino.discord_bot.presentation.table import GameTablePresenter
from casino.domain.enums import CoinSide, GameStatus, GameType
from casino.domain.records import GameRecord
from casino.errors import InsufficientBalanceError
from tests.test_game_service import GameHarness


class _Settings:
    discord_guild_id = 111


def _game(*, guild_id: str | None, private_channel_id: str | None = None) -> GameRecord:
    return GameRecord(
        game_id="g1",
        status=GameStatus.PENDING,
        creator_id="1",
        bet_amount_lamports=50_000_000,
        currency="USD",
        bet_amount_fiat=Decimal("5"),
        guild_id=guild_id,
        private_channel_id=private_channel_id,
    )


def test_home_guild_matches_configured_id() -> None:
    assert is_home_guild(_Settings(), 111)
    assert is_home_guild(_Settings(), "111")
    assert not is_home_guild(_Settings(), 222)
    assert not is_home_guild(_Settings(), None)
    assert not is_home_guild(SimpleNamespace(discord_guild_id=None), 111)


def test_inline_only_outside_home_without_private_table() -> None:
    assert is_inline_game(_Settings(), _game(guild_id=None))
    assert is_inline_game(_Settings(), _game(guild_id="222"))
    assert not is_inline_game(_Settings(), _game(guild_id="111"))
    assert not is_inline_game(_Settings(), _game(guild_id="111", private_channel_id="c1"))
    assert not is_inline_game(_Settings(), _game(guild_id="222", private_channel_id="c1"))
    assert not should_number_game(_Settings(), _game(guild_id=None))
    assert should_number_game(_Settings(), _game(guild_id="111"))


@pytest.mark.asyncio
async def test_join_requires_database_balance() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "0")
    game = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    with pytest.raises(InsufficientBalanceError):
        await harness.service.join_game(game.game_id, "2")


@pytest.mark.asyncio
async def test_app_coinflip_does_not_take_a_history_number() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "10")
    harness.seed_user("2", "10")
    created = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
    )
    assert created.game_number is None
    assert created.display_title() == "Coinflip"
    await harness.service.join_game(created.game_id, "2")
    resolved = await harness.ready_all(created.game_id)
    assert resolved.game_number is None
    assert resolved.display_title() == "Coinflip"


@pytest.mark.asyncio
async def test_cancelled_home_games_do_not_consume_history_numbers() -> None:
    harness = GameHarness(CoinSide.HEADS)
    harness.seed_user("1", "20")
    harness.seed_user("2", "20")
    cancelled_flip = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        guild_id="99",
    )
    await harness.service.cancel_game(cancelled_flip.game_id, "1")
    cancelled_dice = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        game_type=GameType.DICE,
        guild_id="99",
    )
    await harness.service.cancel_game(cancelled_dice.game_id, "1")
    finished = await harness.service.create_game(
        player1_id="1",
        fiat_amount=Decimal("5"),
        side=CoinSide.HEADS,
        guild_id="99",
    )
    await harness.service.join_game(finished.game_id, "2")
    resolved = await harness.ready_all(finished.game_id)
    assert resolved.display_title() == "Coinflip #1"
    assert cancelled_flip.game_number is None
    assert cancelled_dice.game_number is None


def test_v2_edit_kwargs_omit_null_embed() -> None:
    view = discord.ui.LayoutView()
    kwargs = CasinoPanel(view=view, title="Coinflip").edit_kwargs()
    assert kwargs["view"] is view
    assert "embed" not in kwargs


@pytest.mark.asyncio
async def test_inline_edit_uses_interaction_webhook() -> None:
    captured: dict[str, object] = {}

    class _Response:
        def is_done(self) -> bool:
            return True

    class _Interaction:
        response = _Response()

        async def edit_original_response(self, **kwargs: object) -> None:
            captured.update(kwargs)

    view = discord.ui.LayoutView()
    panel = CasinoPanel(view=view, title="Coinflip")
    presenter = GameTablePresenter(SimpleNamespace(), SimpleNamespace(settings=_Settings()))
    assert await presenter._edit_from_interaction(_Interaction(), panel)  # type: ignore[arg-type]
    assert captured["view"] is view
    assert "embed" not in captured
