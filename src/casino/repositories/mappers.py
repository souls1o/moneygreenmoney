from __future__ import annotations

from typing import Any

from casino.domain.enums import (
    CoinSide,
    GameMode,
    GameStatus,
    GameType,
    LedgerEntryType,
    RainStatus,
    TransactionStatus,
    TransactionType,
    UserRole,
)
from casino.domain.records import (
    CursorRecord,
    DiceRollRecord,
    GameRecord,
    GuildConfigRecord,
    HouseTreasuryRecord,
    LedgerRecord,
    RainRecord,
    TransactionRecord,
    UserRecord,
)
from casino.repositories.base import MongoRepository
from casino.utils.clock import DateTimeNormalizer


class DocumentMapper:
    """Bidirectional mapping between domain records and MongoDB documents."""

    def __init__(self, normalizer: DateTimeNormalizer | None = None) -> None:
        self._time = normalizer or DateTimeNormalizer()

    def user_to_document(self, user: UserRecord) -> dict[str, Any]:
        return {
            "discordId": user.discord_id,
            "solBalanceLamports": user.sol_balance_lamports,
            "totalDepositedLamports": user.total_deposited_lamports,
            "totalWithdrawnLamports": user.total_withdrawn_lamports,
            "totalWageredLamports": user.total_wagered_lamports,
            "totalWonLamports": user.total_won_lamports,
            "totalFeesPaidLamports": user.total_fees_paid_lamports,
            "totalWageredUsd": MongoRepository.decimal_to_str(user.total_wagered_usd),
            "totalWonUsd": MongoRepository.decimal_to_str(user.total_won_usd),
            "maxBetUsd": MongoRepository.decimal_to_str(user.max_bet_usd),
            "gamesPlayed": user.games_played,
            "gamesWon": user.games_won,
            "displayCurrency": user.display_currency,
            "walletAddress": user.wallet_address,
            "depositAddress": user.deposit_address,
            "depositMemo": user.deposit_memo,
            "roles": [role.value for role in user.roles],
            "referredBy": user.referred_by,
            "referredAt": user.referred_at,
            "referredWageredUsd": MongoRepository.decimal_to_str(user.referred_wagered_usd),
            "referredWageredLamports": user.referred_wagered_lamports,
            "affiliateEarnedLamports": user.affiliate_earned_lamports,
            "affiliateEarnedUsd": MongoRepository.decimal_to_str(user.affiliate_earned_usd),
            "version": user.version,
            "createdAt": user.created_at,
            "updatedAt": user.updated_at,
        }

    def user_from_document(self, document: dict[str, Any]) -> UserRecord:
        return UserRecord(
            discord_id=document["discordId"],
            sol_balance_lamports=int(document.get("solBalanceLamports", 0)),
            total_deposited_lamports=int(document.get("totalDepositedLamports", 0)),
            total_withdrawn_lamports=int(document.get("totalWithdrawnLamports", 0)),
            total_wagered_lamports=int(document.get("totalWageredLamports", 0)),
            total_won_lamports=int(document.get("totalWonLamports", 0)),
            total_fees_paid_lamports=int(document.get("totalFeesPaidLamports", 0)),
            total_wagered_usd=MongoRepository.str_to_decimal(document.get("totalWageredUsd")),
            total_won_usd=MongoRepository.str_to_decimal(document.get("totalWonUsd")),
            max_bet_usd=MongoRepository.str_to_decimal(document.get("maxBetUsd")),
            games_played=int(document.get("gamesPlayed", 0)),
            games_won=int(document.get("gamesWon", 0)),
            display_currency=document.get("displayCurrency", "USD"),
            wallet_address=document.get("walletAddress"),
            deposit_address=document.get("depositAddress"),
            deposit_memo=document.get("depositMemo", ""),
            roles=[UserRole(role) for role in document.get("roles", ["player"])],
            referred_by=document.get("referredBy") or None,
            referred_at=self._time.to_utc(document.get("referredAt")),
            referred_wagered_usd=MongoRepository.str_to_decimal(document.get("referredWageredUsd")),
            referred_wagered_lamports=int(document.get("referredWageredLamports", 0)),
            affiliate_earned_lamports=int(document.get("affiliateEarnedLamports", 0)),
            affiliate_earned_usd=MongoRepository.str_to_decimal(document.get("affiliateEarnedUsd")),
            version=int(document.get("version", 0)),
            created_at=self._time.to_utc(document.get("createdAt")),
            updated_at=self._time.to_utc(document.get("updatedAt")),
        )

    def game_to_document(self, game: GameRecord) -> dict[str, Any]:
        return {
            "gameId": game.game_id,
            "status": game.status.value,
            "gameType": game.game_type.value,
            "mode": game.mode.value,
            "creatorId": game.creator_id,
            "player1Id": game.player1_id,
            "player2Id": game.player2_id,
            "player1Side": game.player1_side.value,
            "headsIds": list(game.heads_ids),
            "tailsIds": list(game.tails_ids),
            "houseSeatIds": list(game.house_seat_ids),
            "readyIds": list(game.ready_ids),
            "betAmountLamports": game.bet_amount_lamports,
            "currency": game.currency,
            "betAmountFiat": MongoRepository.decimal_to_str(game.bet_amount_fiat),
            "betAmountUsd": MongoRepository.decimal_to_str(game.bet_amount_usd),
            "playerWagerLamports": {key: int(value) for key, value in game.player_wager_lamports.items()},
            "winnerId": game.winner_id,
            "winnerIds": list(game.winner_ids),
            "winnerSide": game.winner_side.value if game.winner_side else None,
            "houseFeeLamports": game.house_fee_lamports,
            "houseFeeUsd": MongoRepository.decimal_to_str(game.house_fee_usd),
            "winnerPayoutLamports": game.winner_payout_lamports,
            "perWinnerPayoutLamports": game.per_winner_payout_lamports,
            "coinResult": game.coin_result.value if game.coin_result else None,
            "coinFlips": [side.value for side in game.coin_flips],
            "version": game.version,
            "guildId": game.guild_id,
            "channelId": game.channel_id,
            "messageId": game.message_id,
            "privateChannelId": game.private_channel_id,
            "privateMessageId": game.private_message_id,
            "serverSeedHash": game.server_seed_hash,
            "serverSeed": game.server_seed,
            "clientSeed": game.client_seed,
            "nonce": game.nonce,
            "ledgerIds": game.ledger_ids,
            "createdAt": game.created_at,
            "resolvedAt": game.resolved_at,
            "expiresAt": game.expires_at,
            "autoFlipAt": game.auto_flip_at,
            "tableNumber": game.table_number,
            "gameNumber": game.game_number,
            "rematchOfGameId": game.rematch_of_game_id,
            "invitedIds": list(game.invited_ids),
            "channelDeleteAt": game.channel_delete_at,
            "maxPlayers": game.max_players,
            "firstTo": game.first_to,
            "scores": dict(game.scores),
            "turnIndex": game.turn_index,
            "roundIndex": game.round_index,
            "rollIndex": game.roll_index,
            "currentRoundRolls": [self._dice_roll_to_document(roll) for roll in game.current_round_rolls],
            "diceRolls": [self._dice_roll_to_document(roll) for roll in game.dice_rolls],
            "diceRollingIds": list(game.dice_rolling_ids),
        }

    def game_from_document(self, document: dict[str, Any]) -> GameRecord:
        coin_result = document.get("coinResult")
        winner_side = document.get("winnerSide")
        heads_ids, tails_ids, creator_id = self._legacy_sides(document)
        winner_ids = list(document.get("winnerIds") or [])
        if not winner_ids and document.get("winnerId"):
            winner_ids = [document["winnerId"]]
        return GameRecord(
            game_id=document["gameId"],
            status=GameStatus(document["status"]),
            creator_id=creator_id,
            bet_amount_lamports=int(document["betAmountLamports"]),
            currency=document.get("currency", "USD"),
            bet_amount_fiat=MongoRepository.str_to_decimal(document.get("betAmountFiat")),
            bet_amount_usd=MongoRepository.str_to_decimal(document.get("betAmountUsd")),
            player_wager_lamports={
                str(key): int(value) for key, value in (document.get("playerWagerLamports") or {}).items()
            },
            game_type=GameType(document.get("gameType", GameType.COINFLIP.value)),
            mode=GameMode(document.get("mode", GameMode.SOLO.value)),
            heads_ids=heads_ids,
            tails_ids=tails_ids,
            house_seat_ids=list(document.get("houseSeatIds") or []),
            ready_ids=list(document.get("readyIds") or []),
            winner_id=document.get("winnerId"),
            winner_ids=winner_ids,
            winner_side=CoinSide(winner_side) if winner_side else None,
            house_fee_lamports=int(document.get("houseFeeLamports", 0)),
            house_fee_usd=MongoRepository.str_to_decimal(document.get("houseFeeUsd")),
            winner_payout_lamports=int(document.get("winnerPayoutLamports", 0)),
            per_winner_payout_lamports=int(document.get("perWinnerPayoutLamports", 0)),
            coin_result=CoinSide(coin_result) if coin_result else None,
            coin_flips=[CoinSide(side) for side in (document.get("coinFlips") or [])],
            version=int(document.get("version", 0)),
            guild_id=document.get("guildId"),
            channel_id=document.get("channelId"),
            message_id=document.get("messageId"),
            private_channel_id=document.get("privateChannelId"),
            private_message_id=document.get("privateMessageId"),
            server_seed_hash=document.get("serverSeedHash", ""),
            server_seed=document.get("serverSeed"),
            client_seed=document.get("clientSeed") or document["gameId"],
            nonce=int(document.get("nonce", 0)),
            ledger_ids=list(document.get("ledgerIds", [])),
            created_at=self._time.to_utc(document.get("createdAt")),
            resolved_at=self._time.to_utc(document.get("resolvedAt")),
            expires_at=self._time.to_utc(document.get("expiresAt")),
            auto_flip_at=self._time.to_utc(document.get("autoFlipAt")),
            table_number=int(document["tableNumber"]) if document.get("tableNumber") is not None else None,
            game_number=int(document["gameNumber"]) if document.get("gameNumber") is not None else None,
            rematch_of_game_id=document.get("rematchOfGameId"),
            invited_ids=list(document.get("invitedIds") or []),
            channel_delete_at=self._time.to_utc(document.get("channelDeleteAt")),
            max_players=int(document.get("maxPlayers") or 2),
            first_to=int(document["firstTo"]) if document.get("firstTo") is not None else None,
            scores={str(key): int(value) for key, value in (document.get("scores") or {}).items()},
            turn_index=int(document.get("turnIndex") or 0),
            round_index=int(document.get("roundIndex") or 1),
            roll_index=int(document.get("rollIndex") or 0),
            current_round_rolls=self._dice_rolls_from_document(document.get("currentRoundRolls")),
            dice_rolls=self._dice_rolls_from_document(document.get("diceRolls")),
            dice_rolling_ids=list(document.get("diceRollingIds") or []),
        )

    def _dice_roll_to_document(self, roll: DiceRollRecord) -> dict[str, Any]:
        return {
            "playerId": roll.player_id,
            "roundIndex": roll.round_index,
            "rollIndex": roll.roll_index,
            "dieA": roll.die_a,
            "dieB": roll.die_b,
            "scores": dict(roll.scores),
        }

    def _dice_rolls_from_document(self, value: object) -> list[DiceRollRecord]:
        if not isinstance(value, list):
            return []
        rolls: list[DiceRollRecord] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            rolls.append(
                DiceRollRecord(
                    player_id=str(item.get("playerId") or ""),
                    round_index=int(item.get("roundIndex") or 1),
                    roll_index=int(item.get("rollIndex") or 0),
                    die_a=int(item.get("dieA") or 1),
                    die_b=int(item.get("dieB") or 1),
                    scores={str(key): int(value) for key, value in (item.get("scores") or {}).items()},
                )
            )
        return rolls

    def _legacy_sides(self, document: dict[str, Any]) -> tuple[list[str], list[str], str]:
        if "headsIds" in document or "tailsIds" in document:
            heads = list(document.get("headsIds") or [])
            tails = list(document.get("tailsIds") or [])
            creator = document.get("creatorId") or document.get("player1Id") or (heads[0] if heads else tails[0])
            return heads, tails, str(creator)
        creator = str(document["player1Id"])
        side = CoinSide(document.get("player1Side", CoinSide.HEADS.value))
        opponent = document.get("player2Id")
        heads: list[str] = []
        tails: list[str] = []
        if side is CoinSide.HEADS:
            heads.append(creator)
            if opponent:
                tails.append(opponent)
        else:
            tails.append(creator)
            if opponent:
                heads.append(opponent)
        return heads, tails, creator

    def guild_config_to_document(self, config: GuildConfigRecord) -> dict[str, Any]:
        return {
            "guildId": config.guild_id,
            "coinflipOutcomesChannelId": config.coinflip_outcomes_channel_id,
            "coinflipCategoryId": config.coinflip_category_id,
            "diceOutcomesChannelId": config.dice_outcomes_channel_id,
            "diceCategoryId": config.dice_category_id,
            "provablyFairChannelId": config.provably_fair_channel_id,
            "openGamesChannelId": config.open_games_channel_id,
            "openCoinflipChannelId": config.open_coinflip_channel_id,
            "openDiceChannelId": config.open_dice_channel_id,
            "depositAlertsChannelId": config.deposit_alerts_channel_id,
            "withdrawalAlertsChannelId": config.withdrawal_alerts_channel_id,
            "houseLedgerChannelId": config.house_ledger_channel_id,
            "rainChannelId": config.rain_channel_id,
            "rainPingRoleId": config.rain_ping_role_id,
            "verifyChannelId": config.verify_channel_id,
            "memberRoleId": config.member_role_id,
            "provablyFairMessageId": config.provably_fair_message_id,
            "verifyMessageId": config.verify_message_id,
            "version": config.version,
            "updatedAt": config.updated_at,
        }

    def guild_config_from_document(self, document: dict[str, Any]) -> GuildConfigRecord:
        return GuildConfigRecord(
            guild_id=document["guildId"],
            coinflip_outcomes_channel_id=document.get("coinflipOutcomesChannelId"),
            coinflip_category_id=document.get("coinflipCategoryId"),
            dice_outcomes_channel_id=document.get("diceOutcomesChannelId"),
            dice_category_id=document.get("diceCategoryId"),
            provably_fair_channel_id=document.get("provablyFairChannelId"),
            open_games_channel_id=document.get("openGamesChannelId"),
            open_coinflip_channel_id=document.get("openCoinflipChannelId"),
            open_dice_channel_id=document.get("openDiceChannelId"),
            deposit_alerts_channel_id=document.get("depositAlertsChannelId"),
            withdrawal_alerts_channel_id=document.get("withdrawalAlertsChannelId"),
            house_ledger_channel_id=document.get("houseLedgerChannelId"),
            rain_channel_id=document.get("rainChannelId"),
            rain_ping_role_id=document.get("rainPingRoleId"),
            verify_channel_id=document.get("verifyChannelId"),
            member_role_id=document.get("memberRoleId"),
            provably_fair_message_id=document.get("provablyFairMessageId"),
            verify_message_id=document.get("verifyMessageId"),
            version=int(document.get("version", 0)),
            updated_at=self._time.to_utc(document.get("updatedAt")),
        )

    def rain_to_document(self, rain: RainRecord) -> dict[str, Any]:
        document: dict[str, Any] = {
            "rainId": rain.rain_id,
            "status": rain.status.value,
            "creatorId": rain.creator_id,
            "amountLamports": rain.amount_lamports,
            "currency": rain.currency,
            "amountFiat": MongoRepository.decimal_to_str(rain.amount_fiat),
            "joinerIds": list(rain.joiner_ids),
            "serverSeedHash": rain.server_seed_hash,
            "clientSeed": rain.client_seed,
            "nonce": rain.nonce,
            "ledgerIds": list(rain.ledger_ids),
            "payoutsLamports": {key: int(value) for key, value in rain.payouts_lamports.items()},
            "fairDigest": rain.fair_digest,
            "version": rain.version,
            "createdAt": rain.created_at,
            "expiresAt": rain.expires_at,
        }
        if rain.guild_id:
            document["guildId"] = rain.guild_id
        if rain.channel_id:
            document["channelId"] = rain.channel_id
        if rain.message_id:
            document["messageId"] = rain.message_id
        if rain.server_seed:
            document["serverSeed"] = rain.server_seed
        if rain.resolved_at:
            document["resolvedAt"] = rain.resolved_at
        return document

    def rain_from_document(self, document: dict[str, Any]) -> RainRecord:
        payouts = document.get("payoutsLamports") or {}
        return RainRecord(
            rain_id=document["rainId"],
            status=RainStatus(document["status"]),
            creator_id=document["creatorId"],
            amount_lamports=int(document["amountLamports"]),
            currency=document.get("currency") or "USD",
            amount_fiat=MongoRepository.str_to_decimal(document.get("amountFiat")),
            guild_id=document.get("guildId"),
            joiner_ids=list(document.get("joinerIds") or []),
            channel_id=document.get("channelId"),
            message_id=document.get("messageId"),
            server_seed_hash=document.get("serverSeedHash") or "",
            server_seed=document.get("serverSeed"),
            client_seed=document.get("clientSeed") or "",
            nonce=int(document.get("nonce") or 0),
            ledger_ids=list(document.get("ledgerIds") or []),
            payouts_lamports={str(key): int(value) for key, value in payouts.items()},
            fair_digest=document.get("fairDigest") or "",
            version=int(document.get("version", 0)),
            created_at=self._time.to_utc(document.get("createdAt")),
            expires_at=self._time.to_utc(document.get("expiresAt")),
            resolved_at=self._time.to_utc(document.get("resolvedAt")),
        )

    def transaction_to_document(self, tx: TransactionRecord) -> dict[str, Any]:
        document: dict[str, Any] = {
            "transactionId": tx.transaction_id,
            "type": tx.type.value,
            "status": tx.status.value,
            "amountLamports": tx.amount_lamports,
            "amountUsd": MongoRepository.decimal_to_str(tx.amount_usd),
            "userId": tx.user_id,
            "metadata": tx.metadata,
            "createdAt": tx.created_at,
        }
        if tx.game_id:
            document["gameId"] = tx.game_id
        if tx.solana_tx_signature:
            document["solanaTxSignature"] = tx.solana_tx_signature
        return document

    def transaction_from_document(self, document: dict[str, Any]) -> TransactionRecord:
        return TransactionRecord(
            transaction_id=document["transactionId"],
            type=TransactionType(document["type"]),
            status=TransactionStatus(document["status"]),
            amount_lamports=int(document["amountLamports"]),
            amount_usd=MongoRepository.str_to_decimal(document.get("amountUsd")),
            user_id=document.get("userId"),
            game_id=document.get("gameId"),
            solana_tx_signature=document.get("solanaTxSignature"),
            metadata=dict(document.get("metadata") or {}),
            created_at=self._time.to_utc(document.get("createdAt")),
        )

    def ledger_to_document(self, entry: LedgerRecord) -> dict[str, Any]:
        return {
            "entryId": entry.entry_id,
            "userId": entry.user_id,
            "type": entry.type.value,
            "amountLamports": entry.amount_lamports,
            "balanceAfterLamports": entry.balance_after_lamports,
            "priceUsdAtTime": MongoRepository.decimal_to_str(entry.price_usd_at_time),
            "description": entry.description,
            "relatedGameId": entry.related_game_id,
            "relatedTxId": entry.related_tx_id,
            "timestamp": entry.timestamp,
        }

    def ledger_from_document(self, document: dict[str, Any]) -> LedgerRecord:
        return LedgerRecord(
            entry_id=document["entryId"],
            user_id=document["userId"],
            type=LedgerEntryType(document["type"]),
            amount_lamports=int(document["amountLamports"]),
            balance_after_lamports=int(document["balanceAfterLamports"]),
            price_usd_at_time=MongoRepository.str_to_decimal(document.get("priceUsdAtTime")),
            description=document.get("description", ""),
            related_game_id=document.get("relatedGameId"),
            related_tx_id=document.get("relatedTxId"),
            timestamp=self._time.to_utc(document.get("timestamp")),
        )

    def house_from_document(self, document: dict[str, Any]) -> HouseTreasuryRecord:
        return HouseTreasuryRecord(
            sol_balance_lamports=int(document.get("solBalanceLamports", 0)),
            total_fees_collected_lamports=int(document.get("totalFeesCollectedLamports", 0)),
            version=int(document.get("version", 0)),
            updated_at=self._time.to_utc(document.get("updatedAt")),
        )

    def cursor_from_document(self, document: dict[str, Any]) -> CursorRecord:
        return CursorRecord(
            name=document["name"],
            value=document["value"],
            updated_at=self._time.to_utc(document.get("updatedAt")),
        )
