from decimal import Decimal

from casino.domain.enums import GameStatus, GameType
from casino.domain.records import GameRecord
from casino.utils.channel_names import GameChannelNamer


def test_coinflip_channel_uses_incrementing_id() -> None:
    game = GameRecord(
        game_id="g1",
        status=GameStatus.PENDING,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("10"),
        game_type=GameType.COINFLIP,
        heads_ids=["1"],
        table_number=1,
    )
    assert GameChannelNamer().for_game(game) == "coinflip-1"


def test_later_tables_keep_counting() -> None:
    assert GameChannelNamer().for_values("coinflip", 12) == "coinflip-12"


def test_unnumbered_title_omits_id() -> None:
    game = GameRecord(
        game_id="cf_app",
        status=GameStatus.PENDING,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("10"),
        game_type=GameType.COINFLIP,
        heads_ids=["1"],
    )
    assert game.display_title() == "Coinflip"


def test_history_title_uses_game_number_not_table_number() -> None:
    game = GameRecord(
        game_id="cf_longid",
        status=GameStatus.COMPLETED,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("10"),
        game_type=GameType.COINFLIP,
        heads_ids=["1"],
        table_number=12,
        game_number=1,
    )
    assert game.display_title() == "Coinflip #1"


def test_dice_history_title_uses_its_own_game_number() -> None:
    game = GameRecord(
        game_id="dc_longid",
        status=GameStatus.COMPLETED,
        creator_id="1",
        bet_amount_lamports=1,
        currency="USD",
        bet_amount_fiat=Decimal("10"),
        game_type=GameType.DICE,
        heads_ids=["1"],
        table_number=1,
        game_number=1,
    )
    assert game.display_title() == "Dice #1"


def test_panel_heading_keeps_game_number_visible() -> None:
    from casino.discord_bot.presentation.builder import EmbedBuilder

    header = EmbedBuilder().title("Coinflip #1")._header_text()
    assert "Coinflip" in header
    assert header.replace("\u2060", "").endswith("#1")
