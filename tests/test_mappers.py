from decimal import Decimal

from casino.domain.enums import CoinSide, GameStatus, TransactionStatus, TransactionType
from casino.domain.records import TransactionRecord
from casino.repositories.mappers import DocumentMapper


class TestDocumentMapper:
    def test_omits_null_solana_signature(self) -> None:
        mapper = DocumentMapper()
        document = mapper.transaction_to_document(
            TransactionRecord(
                transaction_id="tx_1",
                type=TransactionType.BET,
                status=TransactionStatus.CONFIRMED,
                amount_lamports=1,
                amount_usd=Decimal("0"),
            )
        )
        assert "solanaTxSignature" not in document
        assert "gameId" not in document

    def test_includes_on_chain_signature(self) -> None:
        mapper = DocumentMapper()
        document = mapper.transaction_to_document(
            TransactionRecord(
                transaction_id="tx_2",
                type=TransactionType.DEPOSIT,
                status=TransactionStatus.CONFIRMED,
                amount_lamports=1,
                amount_usd=Decimal("0"),
                solana_tx_signature="sig",
            )
        )
        assert document["solanaTxSignature"] == "sig"

    def test_legacy_game_document_maps_to_sides(self) -> None:
        mapper = DocumentMapper()
        game = mapper.game_from_document(
            {
                "gameId": "g1",
                "status": GameStatus.PENDING.value,
                "player1Id": "1",
                "player2Id": "2",
                "player1Side": CoinSide.HEADS.value,
                "betAmountLamports": 1,
                "currency": "USD",
                "betAmountFiat": "10",
            }
        )
        assert game.creator_id == "1"
        assert game.heads_ids == ["1"]
        assert game.tails_ids == ["2"]
        assert game.player1_side is CoinSide.HEADS
        assert game.ready_ids == []

    def test_guild_config_maps_open_games_and_pf_message(self) -> None:
        mapper = DocumentMapper()
        config = mapper.guild_config_from_document(
            {
                "guildId": "1",
                "openGamesChannelId": "10",
                "provablyFairChannelId": "20",
                "provablyFairMessageId": "30",
            }
        )
        assert config.open_games_channel_id == "10"
        assert config.provably_fair_channel_id == "20"
        assert config.provably_fair_message_id == "30"
        document = mapper.guild_config_to_document(config)
        assert document["openGamesChannelId"] == "10"
        assert document["provablyFairMessageId"] == "30"

    def test_guild_config_maps_split_open_game_channels(self) -> None:
        from casino.domain.enums import GameType

        mapper = DocumentMapper()
        config = mapper.guild_config_from_document(
            {
                "guildId": "1",
                "openGamesChannelId": "10",
                "openCoinflipChannelId": "11",
                "openDiceChannelId": "12",
            }
        )
        assert config.open_games_channel_id == "10"
        assert config.open_coinflip_channel_id == "11"
        assert config.open_dice_channel_id == "12"
        assert config.listing_channel_id(GameType.COINFLIP) == "11"
        assert config.listing_channel_id(GameType.DICE) == "12"
        document = mapper.guild_config_to_document(config)
        assert document["openCoinflipChannelId"] == "11"
        assert document["openDiceChannelId"] == "12"

    def test_listing_channel_falls_back_to_legacy_open_games(self) -> None:
        from casino.domain.enums import GameType
        from casino.domain.records import GuildConfigRecord

        config = GuildConfigRecord(guild_id="1", open_games_channel_id="10")
        assert config.listing_channel_id(GameType.COINFLIP) == "10"
        assert config.listing_channel_id(GameType.DICE) == "10"

    def test_guild_config_maps_admin_alert_channels(self) -> None:
        mapper = DocumentMapper()
        config = mapper.guild_config_from_document(
            {
                "guildId": "1",
                "depositAlertsChannelId": "40",
                "withdrawalAlertsChannelId": "50",
                "houseLedgerChannelId": "60",
                "rainChannelId": "70",
                "rainPingRoleId": "80",
                "verifyChannelId": "90",
                "memberRoleId": "93",
                "verifyMessageId": "92",
            }
        )
        assert config.deposit_alerts_channel_id == "40"
        assert config.withdrawal_alerts_channel_id == "50"
        assert config.house_ledger_channel_id == "60"
        assert config.rain_channel_id == "70"
        assert config.rain_ping_role_id == "80"
        assert config.verify_channel_id == "90"
        assert config.member_role_id == "93"
        assert config.verify_message_id == "92"
        document = mapper.guild_config_to_document(config)
        assert document["depositAlertsChannelId"] == "40"
        assert document["withdrawalAlertsChannelId"] == "50"
        assert document["houseLedgerChannelId"] == "60"
        assert document["rainChannelId"] == "70"
        assert document["rainPingRoleId"] == "80"
        assert document["verifyChannelId"] == "90"
        assert document["memberRoleId"] == "93"
        assert document["verifyMessageId"] == "92"

    def test_rain_round_trip(self) -> None:
        from datetime import UTC, datetime

        from casino.domain.enums import RainStatus
        from casino.domain.records import RainRecord

        mapper = DocumentMapper()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        rain = RainRecord(
            rain_id="rn_abc",
            status=RainStatus.PENDING,
            creator_id="1",
            amount_lamports=500_000_000,
            currency="USD",
            amount_fiat=Decimal("50"),
            guild_id="9",
            joiner_ids=["2"],
            channel_id="80",
            message_id="81",
            server_seed_hash="hash",
            server_seed="seed",
            client_seed="client",
            nonce=3,
            created_at=now,
            expires_at=now,
        )
        document = mapper.rain_to_document(rain)
        loaded = mapper.rain_from_document(document)
        assert loaded.rain_id == "rn_abc"
        assert loaded.joiner_ids == ["2"]
        assert loaded.channel_id == "80"
        assert loaded.amount_fiat == Decimal("50")
        assert loaded.status is RainStatus.PENDING
