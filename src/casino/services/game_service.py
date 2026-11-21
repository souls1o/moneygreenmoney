from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.blackjack import (
    BlackjackAction,
    BlackjackHand,
    BlackjackOutcome,
    advance_hand_index,
    compare_hands,
    dealer_should_hit,
    is_natural_blackjack,
    player_finished,
)
from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType, LedgerEntryType, TransactionType
from casino.domain.records import (
    DicePlayResult,
    DiceRollRecord,
    DiceTurnEvent,
    GameRecord,
    HOUSE_USER_ID,
    JackpotRecord,
    PlayerProfileStats,
    is_house_seat,
)
from casino.domain.limbo import limbo_payout_lamports, limbo_profit_lamports, parse_limbo_multiplier
from casino.domain.mines import (
    MINES_TILES,
    mines_gems_available,
    mines_payout_lamports,
    parse_mines_count,
)
from casino.domain.target import closest_winners, parse_target_pick
from casino.errors import GameError, InsufficientHouseReserveError, NotFoundError
from casino.logging.logger import LogContext, get_logger
from casino.repositories.game_repository import GameRepository
from casino.repositories.house_repository import HouseRepository
from casino.repositories.jackpot_repository import JackpotRepository
from casino.repositories.user_repository import UserRepository
from casino.services.affiliate_service import AffiliateService
from casino.services.balance_service import BalanceService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService, SolAmount, house_table_cap_lamports
from casino.utils.rng import ProvablyFairService


class GameService:
    """Create, join, resolve, and expire PvP games with a 3% house fee on the pot."""

    def __init__(
        self,
        game_repository: GameRepository,
        user_repository: UserRepository,
        user_service: UserService,
        balance_service: BalanceService,
        price_service: PriceService,
        validation: ValidationService,
        math_service: MathService,
        rng: ProvablyFairService,
        identifiers: IdentifierFactory,
        clock: Clock,
        unit_of_work: UnitOfWorkFactory,
        settings: Settings,
        house_repository: HouseRepository,
        affiliates: AffiliateService | None = None,
        jackpot_repository: JackpotRepository | None = None,
    ) -> None:
        self._games = game_repository
        self._users = user_repository
        self._user_service = user_service
        self._balances = balance_service
        self._prices = price_service
        self._validation = validation
        self._math = math_service
        self._rng = rng
        self._identifiers = identifiers
        self._clock = clock
        self._uow = unit_of_work
        self._settings = settings
        self._house = house_repository
        self._affiliates = affiliates or AffiliateService(
            user_repository, user_service, balance_service, clock
        )
        self._jackpots = jackpot_repository
        self._logger = get_logger("casino.game")

    async def create_game(
        self,
        *,
        player1_id: str,
        fiat_amount: Decimal,
        side: CoinSide,
        mode: GameMode = GameMode.SOLO,
        game_type: GameType = GameType.COINFLIP,
        guild_id: str | None = None,
        max_players: int = 2,
        first_to: int | None = None,
        limbo_target: Decimal | str | None = None,
        mines_count: int | str | None = None,
    ) -> GameRecord:
        user = await self._user_service.get_or_create(player1_id)
        quote = await self._prices.get_quote()
        currency = user.display_currency
        sol_price = quote.sol_price(currency)
        bet = self._math.fiat_to_sol(fiat_amount, sol_price)
        usd_price = quote.sol_price("USD")
        bet_usd = fiat_amount * usd_price / sol_price
        self._validation.validate_bet(bet_usd)
        if game_type in {GameType.BLACKJACK, GameType.LIMBO, GameType.MINES}:
            await self._require_vs_house_bet_cap(bet)

        now = self._clock.now()
        game_id = self._identifiers.game_id(game_type)
        vs_house = game_type in {GameType.BLACKJACK, GameType.LIMBO, GameType.MINES}
        heads_ids = [player1_id] if side is CoinSide.HEADS else []
        tails_ids = [player1_id] if side is CoinSide.TAILS else []
        target = first_to if first_to is not None else 1
        parsed_limbo = parse_limbo_multiplier(limbo_target) if game_type is GameType.LIMBO else None
        if game_type is GameType.LIMBO and parsed_limbo is None:
            raise GameError("Choose a multiplier from 1.01 to 1,000,000.")
        parsed_mines = parse_mines_count(mines_count) if game_type is GameType.MINES else 0
        if game_type not in {GameType.LIMBO, GameType.MINES}:
            if target < 1 or target > 21:
                raise GameError("First-to must be between 1 and 21.")
        first_to = target
        if vs_house:
            heads_ids = [player1_id]
            tails_ids = [HOUSE_USER_ID]
        if game_type is GameType.DICE:
            if mode is GameMode.TEAM:
                max_players = 4
                if side is CoinSide.TAILS:
                    heads_ids = []
                    tails_ids = [player1_id]
                else:
                    heads_ids = [player1_id]
                    tails_ids = []
            else:
                if max_players not in {2, 3, 4}:
                    raise GameError("Dice tables can have 2, 3, or 4 players.")
                heads_ids = [player1_id]
                tails_ids = []
        if game_type is GameType.TARGET:
            if max_players not in {2, 3, 4}:
                raise GameError("Target tables can have 2, 3, or 4 players.")
            heads_ids = [player1_id]
            tails_ids = []

        with LogContext.bind(userId=player1_id, gameId=game_id):
            async with self._uow.begin() as uow:
                if vs_house:
                    seeds = await self._user_service.take_fair_play(player1_id, session=uow.session)
                    server_seed = seeds.server_seed
                    server_seed_hash = seeds.server_seed_hash
                    client_seed = seeds.client_seed
                    nonce = seeds.nonce
                else:
                    commitment = self._rng.commit()
                    server_seed = commitment.server_seed
                    server_seed_hash = commitment.server_seed_hash
                    client_seed = self._rng.client_seed()
                    nonce = await self._house.take_next_fair_nonce()
                game = GameRecord(
                    game_id=game_id,
                    status=GameStatus.PENDING,
                    creator_id=player1_id,
                    bet_amount_lamports=bet.lamports,
                    currency=currency,
                    bet_amount_fiat=fiat_amount,
                    bet_amount_usd=bet_usd,
                    game_type=game_type,
                    mode=mode,
                    heads_ids=heads_ids,
                    tails_ids=tails_ids,
                    guild_id=guild_id,
                    server_seed_hash=server_seed_hash,
                    server_seed=server_seed,
                    client_seed=client_seed,
                    nonce=nonce,
                    max_players=max_players if game_type in {GameType.DICE, GameType.TARGET} else 2,
                    first_to=first_to,
                    limbo_target=parsed_limbo,
                    mines_count=parsed_mines,
                    created_at=now,
                )
                game.record_wager(player1_id, bet.lamports)
                await self._games.insert(game, session=uow.session)
                mutation = await self._balances.debit(
                    user_id=player1_id,
                    amount=bet,
                    entry_type=LedgerEntryType.BET,
                    transaction_type=TransactionType.BET,
                    price_usd=usd_price,
                    description=f"{game_type.value.title()} wager {game_id}",
                    game_id=game_id,
                    session=uow.session,
                )
                game.ledger_ids.append(mutation.ledger_entry.entry_id)
                if game_type is GameType.BLACKJACK:
                    await self._seat_house_and_deal_blackjack(
                        game, bet, usd_price, bet_usd, session=uow.session
                    )
                if game_type is GameType.LIMBO:
                    await self._play_limbo(game, bet, usd_price, bet_usd, session=uow.session)
                if game_type is GameType.MINES:
                    await self._start_mines(game, bet, usd_price, session=uow.session)
                replaced = await self._games.replace(game, session=uow.session)
                if not replaced:
                    raise GameError("The game could not be stored.")

            self._logger.info(
                "game_created",
                betLamports=bet.lamports,
                fiat=str(fiat_amount),
                currency=currency,
                side=side.value,
                mode=mode.value,
                gameType=game_type.value,
                betUsd=str(bet_usd),
                serverSeedHash=game.server_seed_hash,
            )
        return game

    async def join_game(
        self,
        game_id: str,
        player_id: str,
        side: CoinSide | None = None,
    ) -> GameRecord:
        await self._user_service.get_or_create(player_id)
        existing = await self._require_open_game(game_id)
        if existing.has_player(player_id):
            raise GameError("You are already in this game.")
        if existing.is_awaiting_rematch_accepts() and player_id not in existing.invited_ids:
            raise GameError("This rematch is waiting on the original players.")
        if existing.is_open_ffa():
            join_side = CoinSide.HEADS
        else:
            join_side = side or existing.next_vacant_side() or existing.player1_side.opposite()
        capacity = existing.join_side_capacity()
        if not existing.vacant_on(join_side):
            if existing.is_open_ffa():
                raise GameError("That seat is already full.")
            label = existing.team_label(join_side) if existing.is_dice_team() else join_side.value.title()
            raise GameError(f"{label} is already full.")

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        bet = SolAmount.from_lamports(existing.bet_amount_lamports)

        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                debit = await self._balances.debit(
                    user_id=player_id,
                    amount=bet,
                    entry_type=LedgerEntryType.BET,
                    transaction_type=TransactionType.BET,
                    price_usd=usd_price,
                    description=f"{existing.game_type.value.title()} wager {game_id}",
                    game_id=game_id,
                    session=uow.session,
                )
                claimed = await self._games.try_add_to_side(
                    game_id,
                    player_id,
                    join_side,
                    capacity,
                    session=uow.session,
                )
                if claimed is None:
                    await self._balances.credit(
                        user_id=player_id,
                        amount=bet,
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"Join failed refund {game_id}",
                        game_id=game_id,
                        session=uow.session,
                    )
                    raise GameError("That seat was taken or the game is no longer open.")
                claimed.ledger_ids.append(debit.ledger_entry.entry_id)
                claimed.record_wager(player_id, bet.lamports)
                saved = await self._persist_or_start(claimed, usd_price, session=uow.session)
            self._logger.info("game_seat_taken", side=join_side.value, playerId=player_id)
        return saved

    async def call_bot(self, game_id: str, actor_id: str, side: CoinSide | None = None) -> GameRecord:
        existing = await self._require_open_game(game_id)
        if actor_id != existing.creator_id:
            raise GameError("Only the table creator can call the bot.")
        if existing.is_open_ffa():
            house_side = CoinSide.HEADS
        else:
            house_side = side or existing.next_vacant_side()
        if house_side is None:
            raise GameError("The table is already full.")
        if not existing.vacant_on(house_side):
            if existing.is_open_ffa():
                raise GameError("The table is already full.")
            label = existing.team_label(house_side) if existing.is_dice_team() else house_side.value.title()
            raise GameError(f"{label} is already full.")
        house_seat_id = existing.next_house_seat_id()
        capacity = existing.join_side_capacity()

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        await self._require_call_bot_cap(existing)
        bet = SolAmount.from_lamports(existing.bet_amount_lamports)

        with LogContext.bind(userId=actor_id, gameId=game_id):
            async with self._uow.begin() as uow:
                _treasury, house_bet = await self._balances.debit_house_reserve(
                    amount=bet,
                    price_usd=usd_price,
                    description=f"House call-bot wager {game_id}",
                    game_id=game_id,
                    session=uow.session,
                )
                claimed = await self._games.try_add_to_side(
                    game_id,
                    house_seat_id,
                    house_side,
                    capacity,
                    house_seat=True,
                    session=uow.session,
                )
                if claimed is None:
                    await self._balances.credit_house_reserve(
                        amount=bet,
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"House call-bot refund {game_id}",
                        game_id=game_id,
                        session=uow.session,
                    )
                    raise GameError("Someone else filled this seat first.")
                claimed.ledger_ids.append(house_bet.entry_id)
                if claimed.is_target():
                    self._fill_house_target_picks(claimed)
                    if claimed.target_ready_to_resolve():
                        saved = await self._complete_target(claimed, usd_price, session=uow.session)
                    else:
                        saved = await self._persist_or_start(claimed, usd_price, session=uow.session)
                else:
                    saved = await self._persist_or_start(claimed, usd_price, session=uow.session)
            self._logger.info("house_called", actorId=actor_id, houseSide=house_side.value, houseSeatId=house_seat_id)
        return saved

    async def lock_target_pick(self, game_id: str, player_id: str, number: object) -> GameRecord:
        pick = parse_target_pick(number)
        existing = await self._require_open_game(game_id)
        if not existing.is_target():
            raise GameError("This is not a target table.")
        if is_house_seat(player_id) or not existing.has_player(player_id):
            raise GameError("Sit at this table before locking a number.")
        if existing.has_target_pick(player_id):
            raise GameError("Your number is already locked.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                game = await self._games.find_by_id(game_id, session=uow.session)
                if game is None or game.status is not GameStatus.PENDING or not game.is_target():
                    raise GameError("This target table is no longer open.")
                if not game.has_player(player_id) or is_house_seat(player_id):
                    raise GameError("Sit at this table before locking a number.")
                if game.has_target_pick(player_id):
                    raise GameError("Your number is already locked.")
                game.target_picks[player_id] = pick
                self._fill_house_target_picks(game)
                if game.target_ready_to_resolve():
                    saved = await self._complete_target(game, usd_price, session=uow.session)
                else:
                    saved = await self._persist_or_start(game, usd_price, session=uow.session)
            self._logger.info("target_pick_locked", pick=pick, status=saved.status.value)
        return saved

    def _fill_house_target_picks(self, game: GameRecord) -> None:
        if not game.is_target() or not game.server_seed:
            return
        for index, seat_id in enumerate(game.house_seat_ids):
            if seat_id in game.target_picks:
                continue
            fair = self._rng.resolve_target(game.server_seed, game.client_seed, game.nonce, index + 1)
            game.target_picks[seat_id] = fair.value

    async def _complete_target(self, game: GameRecord, usd_price: Decimal, *, session) -> GameRecord:
        if not game.server_seed:
            raise GameError("This game is missing its provably fair seed.")
        fair = self._rng.resolve_target(game.server_seed, game.client_seed, game.nonce, 0)
        game.target_number = fair.value
        winners = closest_winners(game.target_picks, fair.value)
        if not winners:
            return await self._refund_target_push(game, usd_price, session=session)
        await self._payout_winners(game, winners, usd_price, session=session)
        replaced = await self._games.replace(game, session=session)
        if not replaced:
            raise GameError("The game could not be updated due to a concurrent change.")
        return game

    async def _refund_target_push(self, game: GameRecord, usd_price: Decimal, *, session) -> GameRecord:
        now = self._clock.now()
        await self._assign_game_number(game, session=session)
        for player_id in game.human_player_ids():
            refund = await self._balances.credit(
                user_id=player_id,
                amount=game.wager_for(player_id),
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Target push {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(refund.ledger_entry.entry_id)
            await self._users.apply_game_stats(
                player_id,
                wagered=game.wager_for(player_id),
                wagered_usd=self._math.sol_to_fiat(game.wager_for(player_id), usd_price),
                max_bet_usd=game.bet_amount_usd,
                fees_paid=SolAmount.zero(),
                won=False,
                won_usd=Decimal("0"),
                now=now,
                session=session,
            )
        for seat_id in game.house_seat_ids:
            _treasury, house_refund = await self._balances.credit_house_reserve(
                amount=game.wager_for(seat_id),
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Target push house {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(house_refund.entry_id)
        game.status = GameStatus.COMPLETED
        game.winner_ids = []
        game.winner_id = None
        game.house_fee_lamports = 0
        game.resolved_at = now
        game.auto_flip_at = None
        replaced = await self._games.replace(game, session=session)
        if not replaced:
            raise GameError("The game could not be updated due to a concurrent change.")
        self._logger.info("game_resolved", winnerIds=[], houseFeeLamports=0, payoutLamports=0, gameType=game.game_type.value)
        return game

    async def mark_ready(self, game_id: str, player_id: str) -> GameRecord:
        existing = await self._require_open_game(game_id)
        if is_house_seat(player_id) or not existing.has_player(player_id):
            raise GameError("Only seated players can ready up.")
        if existing.is_ready(player_id):
            raise GameError("You are already ready.")

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")

        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                claimed = await self._games.try_mark_ready(game_id, player_id, session=uow.session)
                if claimed is None:
                    raise GameError("You cannot ready this game.")
                if claimed.can_start():
                    started = await self._persist_or_start(claimed, usd_price, session=uow.session)
                    self._logger.info("player_ready", playerId=player_id, started=True)
                    return started
            self._logger.info("player_ready", playerId=player_id, started=False)
        return claimed

    async def list_completed(self, guild_id: str, limit: int = 10) -> list[GameRecord]:
        return await self._games.list_completed(guild_id, limit=limit)

    async def cancel_game(self, game_id: str, actor_id: str, *, admin: bool = False) -> GameRecord:
        game = await self._games.find_by_id(game_id)
        if game is None:
            raise NotFoundError("That game was not found.")
        if game.status != GameStatus.PENDING:
            raise GameError("Only waiting games can be cancelled.")
        if not admin and game.creator_id != actor_id:
            raise GameError("Only the creator can cancel this game.")
        return await self._refund_open_game(game, GameStatus.CANCELLED, "Game cancelled")

    async def leave_game(self, game_id: str, player_id: str) -> GameRecord:
        existing = await self._require_open_game(game_id)
        if is_house_seat(player_id) or not existing.has_player(player_id):
            raise GameError("You are not seated at this table.")

        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        refund = existing.wager_for(player_id)
        now = self._clock.now()

        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                claimed = await self._games.try_remove_player(game_id, player_id, session=uow.session)
                if claimed is None:
                    raise GameError("You cannot leave this game.")
                mutation = await self._balances.credit(
                    user_id=player_id,
                    amount=refund,
                    entry_type=LedgerEntryType.REFUND,
                    transaction_type=TransactionType.REFUND,
                    price_usd=usd_price,
                    description=f"Left table {game_id}",
                    game_id=game_id,
                    session=uow.session,
                )
                claimed.ledger_ids.append(mutation.ledger_entry.entry_id)
                if not claimed.human_player_ids():
                    emptied = await self._refund_house_seats(claimed, usd_price, "Table emptied", session=uow.session)
                    emptied.status = GameStatus.CANCELLED
                    emptied.resolved_at = now
                    emptied.auto_flip_at = None
                    replaced = await self._games.replace(emptied, session=uow.session)
                    if not replaced:
                        raise GameError("The table could not be closed.")
                    self._logger.info("player_left", emptied=True)
                    return emptied
                if claimed.creator_id == player_id:
                    claimed.creator_id = claimed.human_player_ids()[0]
                self._sync_auto_flip(claimed, now)
                replaced = await self._games.replace(claimed, session=uow.session)
                if not replaced:
                    raise GameError("The table could not be updated due to a concurrent change.")
                self._logger.info("player_left", emptied=False, remaining=len(claimed.human_player_ids()))
        return claimed

    async def expire_open_games(self) -> list[GameRecord]:
        return await self.auto_flip_due_games()

    async def auto_flip_due_games(self) -> list[GameRecord]:
        due = await self._games.list_due_auto_flips(self._clock.now())
        flipped: list[GameRecord] = []
        for game in due:
            try:
                resolved = await self._auto_flip(game)
                if resolved.status is GameStatus.COMPLETED or resolved.is_awaiting_settlement():
                    flipped.append(resolved)
            except Exception:
                self._logger.exception("auto_flip_failed", gameId=game.game_id)
        return flipped

    async def get(self, game_id: str) -> GameRecord:
        game = await self._games.find_by_id(game_id)
        if game is None:
            raise NotFoundError("That game was not found.")
        return game

    async def history_for(
        self,
        discord_id: str,
        *,
        page: int = 0,
        limit: int = 10,
    ) -> tuple[list[GameRecord], int]:
        games = await self._completed_history_games(discord_id)
        skip = max(page, 0) * limit
        return games[skip : skip + limit], len(games)

    async def profile_stats(self, discord_id: str) -> PlayerProfileStats:
        games = await self._completed_history_games(discord_id)
        if not games:
            return PlayerProfileStats()
        wagered_usd = Decimal("0")
        wagered_lamports = 0
        profit_usd = Decimal("0")
        profit_lamports = 0
        wins = 0
        counts: dict[str, int] = {}
        for game in games:
            wagered_usd += game.stake_usd()
            wagered_lamports += game.wager_for(discord_id).lamports
            profit_usd += game.player_profit_fiat(discord_id)
            profit_lamports += game.player_profit_lamports(discord_id)
            if game.player_won(discord_id):
                wins += 1
            label = game.game_type.value.title()
            counts[label] = counts.get(label, 0) + 1
        favorite = max(counts, key=lambda name: (counts[name], name))
        return PlayerProfileStats(
            games_played=len(games),
            games_won=wins,
            wagered_usd=wagered_usd,
            wagered_lamports=wagered_lamports,
            profit_usd=profit_usd,
            profit_lamports=profit_lamports,
            favorite_game=favorite,
        )

    async def _completed_history_games(self, discord_id: str) -> list[GameRecord]:
        games = list(await self._games.list_completed_by_player(discord_id))
        if self._jackpots is not None:
            for jackpot in await self._jackpots.list_completed_by_player(discord_id):
                games.append(await self._jackpot_as_history_game(jackpot, discord_id))
        games.sort(key=self._history_sort_key, reverse=True)
        return games

    async def _jackpot_as_history_game(self, jackpot: JackpotRecord, discord_id: str) -> GameRecord:
        stake = int(jackpot.entries_lamports.get(discord_id, 0))
        stake_usd = await self._usd_for_lamports(stake)
        winner_ids = [jackpot.winner_id] if jackpot.winner_id else []
        return GameRecord(
            game_id=jackpot.jackpot_id,
            status=GameStatus.COMPLETED,
            creator_id=jackpot.opener_id or discord_id,
            bet_amount_lamports=stake,
            currency="USD",
            bet_amount_fiat=stake_usd,
            bet_amount_usd=stake_usd,
            player_wager_lamports=dict(jackpot.entries_lamports),
            game_type=GameType.JACKPOT,
            heads_ids=list(jackpot.entries_lamports),
            winner_id=jackpot.winner_id,
            winner_ids=winner_ids,
            winner_payout_lamports=int(jackpot.winner_payout_lamports),
            per_winner_payout_lamports=int(jackpot.winner_payout_lamports),
            house_fee_lamports=int(jackpot.house_fee_lamports),
            max_players=max(len(jackpot.entries_lamports), 1),
            resolved_at=jackpot.resolved_at or jackpot.created_at,
            created_at=jackpot.created_at,
        )

    async def _usd_for_lamports(self, lamports: int) -> Decimal:
        if lamports <= 0:
            return Decimal("0")
        quote = await self._prices.get_quote(allow_stale=True)
        return self._math.sol_to_fiat(SolAmount.from_lamports(lamports), quote.sol_price("USD"))

    @staticmethod
    def _history_sort_key(game: GameRecord) -> datetime:
        moment = game.resolved_at or game.created_at
        if moment is None:
            return datetime.min.replace(tzinfo=UTC)
        if moment.tzinfo is None:
            return moment.replace(tzinfo=UTC)
        return moment

    async def attach_listing(self, game_id: str, channel_id: str, message_id: str) -> None:
        await self._games.set_listing_ref(game_id, channel_id, message_id)

    async def attach_private(self, game_id: str, channel_id: str, message_id: str) -> None:
        await self._games.set_private_ref(game_id, channel_id, message_id)

    async def attach_guild(self, game_id: str, guild_id: str) -> None:
        await self._games.set_guild_id(game_id, guild_id)

    async def attach_table_number(self, game_id: str, table_number: int) -> None:
        await self._games.set_table_number(game_id, table_number)

    async def start_rematch(self, source_game_id: str, actor_id: str) -> GameRecord:
        source = await self.get(source_game_id)
        if source.status is not GameStatus.COMPLETED:
            raise GameError("You can only rerun a finished match.")
        if actor_id not in source.human_player_ids():
            raise GameError("Only players from this match can rerun it.")
        if not source.private_channel_id:
            raise GameError("This table has no private channel to rerun in.")
        existing = await self._games.find_pending_in_channel(source.private_channel_id)
        if existing is not None:
            raise GameError("A rematch is already waiting in this channel.")
        side = CoinSide.HEADS if source.is_dice_ffa() or actor_id in source.heads_ids else CoinSide.TAILS
        invited = [player_id for player_id in source.human_player_ids() if player_id != actor_id]
        now = self._clock.now()
        delay = self._settings.table_idle_delete_seconds
        rematch = await self.create_game(
            player1_id=actor_id,
            fiat_amount=source.bet_amount_fiat,
            side=side,
            mode=source.mode,
            game_type=source.game_type,
            guild_id=source.guild_id,
            max_players=source.max_players,
            first_to=source.first_to,
        )
        rematch.rematch_of_game_id = source.game_id
        rematch.invited_ids = invited
        rematch.table_number = source.table_number
        rematch.private_channel_id = source.private_channel_id
        if rematch.is_awaiting_rematch_accepts():
            rematch.channel_delete_at = now + timedelta(seconds=delay)
        replaced = await self._games.replace(rematch)
        if not replaced:
            await self.cancel_game(rematch.game_id, actor_id, admin=True)
            raise GameError("The rematch could not be stored.")
        try:
            rematch = await self._fill_house_from_source(rematch, source, actor_id)
        except (GameError, InsufficientHouseReserveError):
            await self.cancel_game(rematch.game_id, actor_id, admin=True)
            raise
        await self._games.clear_channel_delete_at(source.game_id)
        self._logger.info("rematch_started", sourceGameId=source.game_id, gameId=rematch.game_id)
        return rematch

    async def _fill_house_from_source(self, rematch: GameRecord, source: GameRecord, actor_id: str) -> GameRecord:
        current = rematch
        if source.is_dice_ffa():
            needed = source.house_seat_count()
            for _ in range(needed):
                current = await self.call_bot(current.game_id, actor_id)
            return current
        for side in (CoinSide.HEADS, CoinSide.TAILS):
            needed = sum(1 for player_id in source.ids_for(side) if is_house_seat(player_id))
            for _ in range(needed):
                current = await self.call_bot(current.game_id, actor_id, side)
        return current

    async def accept_rematch(self, game_id: str, actor_id: str) -> GameRecord:
        game = await self._require_open_game(game_id)
        if not game.rematch_of_game_id:
            raise GameError("This is not a rematch invite.")
        if actor_id == game.creator_id:
            raise GameError("You already started this rematch.")
        if actor_id not in game.invited_ids:
            raise GameError("Only the other players from the last match can accept.")
        source = await self.get(game.rematch_of_game_id)
        side = CoinSide.HEADS if source.is_dice_ffa() or actor_id in source.heads_ids else CoinSide.TAILS
        joined = await self.join_game(game_id, actor_id, side)
        if not joined.is_awaiting_rematch_accepts():
            await self._games.clear_channel_delete_at(joined.game_id)
            joined.channel_delete_at = None
        return joined

    async def decline_rematch(self, game_id: str, actor_id: str) -> GameRecord:
        game = await self._require_open_game(game_id)
        if not game.rematch_of_game_id:
            raise GameError("This is not a rematch invite.")
        if actor_id not in game.invited_ids:
            raise GameError("Only the other players from the last match can decline.")
        cancelled = await self.cancel_game(game_id, actor_id, admin=True)
        self._logger.info("rematch_declined", gameId=game_id, actorId=actor_id)
        return cancelled

    async def close_idle_tables(self) -> list[GameRecord]:
        due = await self._games.list_due_channel_deletes(self._clock.now())
        closed: list[GameRecord] = []
        seen: set[str] = set()
        for game in due:
            channel_id = game.private_channel_id
            if not channel_id or channel_id in seen:
                continue
            pending = await self._games.find_pending_in_channel(channel_id)
            if pending is not None:
                delete_at = self._clock.to_utc(pending.channel_delete_at)
                now = self._clock.to_utc(self._clock.now())
                if delete_at and now and delete_at > now:
                    continue
                try:
                    closed.append(await self.cancel_game(pending.game_id, pending.creator_id, admin=True))
                except Exception:
                    self._logger.exception("idle_rematch_refund_failed", gameId=pending.game_id)
                    continue
                await self._games.clear_channel_delete_at(pending.game_id)
            else:
                closed.append(game)
            await self._games.clear_channel_delete_at(game.game_id)
            seen.add(channel_id)
        return closed

    async def _persist_or_start(self, game: GameRecord, usd_price: Decimal, *, session) -> GameRecord:
        self._sync_auto_flip(game, self._clock.now())
        if game.can_start():
            if game.is_dice():
                started, _events = await self._activate_dice(game, usd_price, session=session)
                return started
            if game.is_blackjack():
                replaced = await self._games.replace(game, session=session)
                if not replaced:
                    raise GameError("The game could not be updated due to a concurrent change.")
                return game
            return await self._resolve(game, usd_price, session=session)
        replaced = await self._games.replace(game, session=session)
        if not replaced:
            raise GameError("The game could not be updated due to a concurrent change.")
        return game

    async def _auto_flip(self, game: GameRecord) -> GameRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        async with self._uow.begin() as uow:
            current = await self._games.find_by_id(game.game_id, session=uow.session)
            if current is None or current.status is not GameStatus.PENDING:
                return current or game
            if not current.is_full():
                current.auto_flip_at = None
                await self._games.replace(current, session=uow.session)
                return current
            if current.is_target():
                current.auto_flip_at = None
                await self._games.replace(current, session=uow.session)
                return current
            if current.is_dice():
                started, _events = await self._activate_dice(current, usd_price, session=uow.session)
                self._logger.info("dice_auto_started", gameId=game.game_id)
                return started
            resolved = await self._resolve(current, usd_price, session=uow.session)
        self._logger.info("game_auto_flipped", gameId=game.game_id)
        return resolved

    def _sync_auto_flip(self, game: GameRecord, now) -> None:
        if game.is_target() or game.is_blackjack() or game.is_limbo() or game.is_mines():
            game.auto_flip_at = None
            return
        if game.is_full():
            if game.auto_flip_at is None:
                delay = self._settings.auto_flip_after_seconds
                game.auto_flip_at = now + timedelta(seconds=delay)
            return
        game.auto_flip_at = None

    async def roll_dice(self, channel_id: str, player_id: str) -> DicePlayResult:
        game = await self._games.find_active_in_channel(channel_id)
        if game is None or not game.is_dice():
            raise GameError("There is no active dice game in this channel.")
        if game.status is not GameStatus.ACTIVE:
            raise GameError("This dice game has not started yet.")
        if not game.has_player(player_id) or is_house_seat(player_id):
            raise GameError("You are not seated at this table.")
        if game.current_turn_id() != player_id:
            raise GameError("It's not your turn.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=player_id, gameId=game.game_id):
            async with self._uow.begin() as uow:
                current = await self._games.find_by_id(game.game_id, session=uow.session)
                if current is None or current.status is not GameStatus.ACTIVE:
                    raise GameError("This dice game is no longer active.")
                if current.current_turn_id() != player_id:
                    raise GameError("It's not your turn.")
                events: list[DiceTurnEvent] = []
                events.append(await self._apply_dice_roll(current, player_id, usd_price, session=uow.session))
                replaced = await self._games.replace(current, session=uow.session)
                if not replaced:
                    raise GameError("The roll could not be stored due to a concurrent update.")
            self._logger.info("dice_rolled", playerId=player_id, roundIndex=current.round_index)
        return DicePlayResult(game=current, events=events)

    async def play_next_house_turn(self, channel_id: str) -> DicePlayResult:
        game = await self._games.find_active_in_channel(channel_id)
        if game is None or not game.is_dice():
            raise GameError("There is no active dice game in this channel.")
        if game.status is not GameStatus.ACTIVE:
            raise GameError("This dice game has not started yet.")
        seat = game.current_turn_id()
        if seat is None or not is_house_seat(seat):
            raise GameError("It is not the house's turn.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=seat, gameId=game.game_id):
            async with self._uow.begin() as uow:
                current = await self._games.find_by_id(game.game_id, session=uow.session)
                if current is None or current.status is not GameStatus.ACTIVE:
                    raise GameError("This dice game is no longer active.")
                house_id = current.current_turn_id()
                if house_id is None or not is_house_seat(house_id):
                    raise GameError("It is not the house's turn.")
                events = [await self._apply_dice_roll(current, house_id, usd_price, session=uow.session)]
                replaced = await self._games.replace(current, session=uow.session)
                if not replaced:
                    raise GameError("The roll could not be stored due to a concurrent update.")
            self._logger.info("dice_house_rolled", playerId=house_id, roundIndex=current.round_index)
        return DicePlayResult(game=current, events=events)

    async def _activate_dice(
        self, game: GameRecord, usd_price: Decimal, *, session
    ) -> tuple[GameRecord, list[DiceTurnEvent]]:
        game.status = GameStatus.ACTIVE
        game.auto_flip_at = None
        game.scores = {player_id: 0 for player_id in game.player_ids()}
        game.turn_index = 0
        game.round_index = 1
        game.roll_index = 0
        game.current_round_rolls = []
        game.dice_rolls = []
        game.dice_rolling_ids = []
        events = await self._play_house_turns(game, usd_price, session=session)
        replaced = await self._games.replace(game, session=session)
        if not replaced:
            raise GameError("The dice game could not be started.")
        return game, events

    async def _play_house_turns(
        self, game: GameRecord, usd_price: Decimal, *, session
    ) -> list[DiceTurnEvent]:
        events: list[DiceTurnEvent] = []
        while game.status is GameStatus.ACTIVE:
            current = game.current_turn_id()
            if current is None or not is_house_seat(current):
                break
            events.append(await self._apply_dice_roll(game, current, usd_price, session=session))
        return events

    async def _apply_dice_roll(
        self, game: GameRecord, player_id: str, usd_price: Decimal, *, session
    ) -> DiceTurnEvent:
        if not game.server_seed:
            raise GameError("This game is missing its provably fair seed.")
        fair = self._rng.resolve_dice(game.server_seed, game.client_seed, game.nonce, game.roll_index)
        roll = DiceRollRecord(
            player_id=player_id,
            round_index=game.round_index,
            roll_index=game.roll_index,
            die_a=fair.die_a,
            die_b=fair.die_b,
        )
        game.dice_rolls.append(roll)
        game.current_round_rolls.append(roll)
        game.roll_index += 1
        rollers = game.rolling_player_ids()
        game.turn_index = (game.turn_index + 1) % max(len(rollers), 1)
        round_winner_id: str | None = None
        round_tied = False
        match_over = False
        roll_off_ids: tuple[str, ...] = ()
        if len(game.current_round_rolls) >= len(rollers):
            totals = {entry.player_id: entry.total for entry in game.current_round_rolls}
            best = max(totals.values())
            leaders = [seat_id for seat_id in rollers if totals.get(seat_id) == best]
            unique_winner = self._dice_round_winner(game, leaders)
            if unique_winner is not None:
                self._award_dice_round(game, unique_winner)
                round_winner_id = unique_winner
                game.dice_rolling_ids = []
                game.current_round_rolls = []
                game.round_index += 1
                game.turn_index = 0
                if game.has_reached_first_to():
                    match_over = True
                    await self._payout_winners(
                        game, self._dice_match_winners(game), usd_price, session=session
                    )
            elif game.is_dice_team() or len(game.player_ids()) >= 3:
                game.dice_rolling_ids = leaders
                game.current_round_rolls = []
                game.turn_index = 0
                round_tied = True
                roll_off_ids = tuple(leaders)
            else:
                round_tied = True
                game.dice_rolling_ids = []
                game.current_round_rolls = []
                game.round_index += 1
                game.turn_index = 0
        snapshot = {seat_id: game.score_for(seat_id) for seat_id in game.player_ids()}
        roll = replace(roll, scores=snapshot)
        game.dice_rolls[-1] = roll
        if game.current_round_rolls:
            game.current_round_rolls[-1] = roll
        return DiceTurnEvent(
            roll=roll,
            round_winner_id=round_winner_id,
            round_tied=round_tied,
            match_over=match_over,
            roll_off_ids=roll_off_ids,
        )

    def _dice_round_winner(self, game: GameRecord, leaders: list[str]) -> str | None:
        if not leaders:
            return None
        if game.is_dice_team():
            sides = {game.side_of(player_id) for player_id in leaders}
            sides.discard(None)
            if len(sides) == 1:
                return leaders[0]
            return None
        if len(leaders) == 1:
            return leaders[0]
        return None

    def _award_dice_round(self, game: GameRecord, winner_id: str) -> None:
        if game.is_dice_team():
            side = game.side_of(winner_id)
            if side is not None:
                for teammate_id in game.ids_for(side):
                    game.scores[teammate_id] = game.score_for(teammate_id) + 1
                return
        game.scores[winner_id] = game.score_for(winner_id) + 1

    def _dice_match_winners(self, game: GameRecord) -> list[str]:
        champion = max(game.scores, key=lambda player_id: game.scores[player_id])
        if game.is_dice_team():
            side = game.side_of(champion)
            if side is not None:
                return list(game.ids_for(side))
        return [champion]

    async def _require_open_game(self, game_id: str) -> GameRecord:
        existing = await self._games.find_by_id(game_id)
        if existing is None:
            raise NotFoundError("That game was not found.")
        if existing.status != GameStatus.PENDING:
            raise GameError("This game is no longer open.")
        return existing

    async def _payout_winners(
        self, game: GameRecord, winners: list[str], usd_price: Decimal, *, session
    ) -> GameRecord:
        bet = SolAmount.from_lamports(game.bet_amount_lamports)
        split = self._math.split_player_pot(bet, len(game.player_ids()))
        bet_usd = self._math.sol_to_fiat(bet, usd_price)
        if not winners:
            raise GameError("The winning side has no players.")
        display_shares = self._math.split_evenly(split.winner_payout, len(winners))
        gross_shares = self._math.split_evenly(split.total_pot, len(winners))
        collected_fee = self._math.collected_house_fee(
            split.house_fee,
            human_winner_count=sum(1 for winner_id in winners if not is_house_seat(winner_id)),
            winner_count=len(winners),
        )
        fee_usd = self._math.sol_to_fiat(collected_fee, usd_price)
        now = self._clock.now()
        await self._assign_game_number(game, session=session)

        for winner_id, display_share, gross_share in zip(
            winners, display_shares, gross_shares, strict=True
        ):
            if is_house_seat(winner_id):
                _treasury, win_entry = await self._balances.credit_house_reserve(
                    amount=gross_share,
                    entry_type=LedgerEntryType.WIN,
                    transaction_type=TransactionType.WIN,
                    price_usd=usd_price,
                    description=f"{game.game_type.value.title()} win {game.game_id}",
                    game_id=game.game_id,
                    session=session,
                )
                game.ledger_ids.append(win_entry.entry_id)
            else:
                win = await self._balances.credit(
                    user_id=winner_id,
                    amount=display_share,
                    entry_type=LedgerEntryType.WIN,
                    transaction_type=TransactionType.WIN,
                    price_usd=usd_price,
                    description=f"{game.game_type.value.title()} win {game.game_id}",
                    game_id=game.game_id,
                    won=display_share,
                    session=session,
                )
                game.ledger_ids.append(win.ledger_entry.entry_id)

        if not collected_fee.is_zero():
            fee_entry = await self._balances.credit_house_fee(
                amount=collected_fee,
                price_usd=usd_price,
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(fee_entry.entry_id)

        humans = game.human_player_ids()
        winner_fee_shares = self._math.split_evenly(split.house_fee, len(winners))
        fees_by_winner = {
            winner_id: share
            for winner_id, share in zip(winners, winner_fee_shares, strict=True)
            if not is_house_seat(winner_id)
        }
        payout_usd_by_winner = {
            winner_id: self._math.sol_to_fiat(share, usd_price)
            for winner_id, share in zip(winners, display_shares, strict=True)
            if not is_house_seat(winner_id)
        }
        for player_id in humans:
            await self._users.apply_game_stats(
                player_id,
                wagered=bet,
                wagered_usd=bet_usd,
                max_bet_usd=bet_usd,
                fees_paid=fees_by_winner.get(player_id, SolAmount.zero()),
                won=player_id in winners,
                won_usd=payout_usd_by_winner.get(player_id, Decimal("0")),
                now=now,
                session=session,
            )

        if not collected_fee.is_zero():
            affiliate_ids = await self._affiliates.settle_game(
                game,
                collected_fee=collected_fee,
                price_usd=usd_price,
                winners=winners,
                session=session,
            )
            game.ledger_ids.extend(affiliate_ids)

        game.status = GameStatus.COMPLETED
        game.winner_ids = winners
        game.winner_id = winners[0] if winners else None
        game.house_fee_lamports = collected_fee.lamports
        game.house_fee_usd = fee_usd
        game.winner_payout_lamports = split.winner_payout.lamports
        game.per_winner_payout_lamports = display_shares[0].lamports
        game.resolved_at = now
        delay = self._settings.table_idle_delete_seconds
        if game.private_channel_id:
            game.channel_delete_at = now + timedelta(seconds=delay)
        self._logger.info(
            "game_resolved",
            winnerIds=winners,
            houseFeeLamports=collected_fee.lamports,
            payoutLamports=split.winner_payout.lamports,
            gameType=game.game_type.value,
        )
        return game

    async def _assign_game_number(self, game: GameRecord, *, session) -> None:
        if game.game_number is not None:
            return
        if not self._should_number_game(game):
            return
        game.game_number = await self._house.take_next_game_number(game.game_type, session=session)

    def _should_number_game(self, game: GameRecord) -> bool:
        home = getattr(self._settings, "discord_guild_id", None)
        if home is not None and game.guild_id is not None and str(game.guild_id) == str(home):
            return True
        return game.private_channel_id is not None

    async def _resolve(self, game: GameRecord, usd_price: Decimal, *, session) -> GameRecord:
        if not game.server_seed:
            raise GameError("This game is missing its provably fair seed.")
        target = game.first_to or 1
        scores = {CoinSide.HEADS.value: 0, CoinSide.TAILS.value: 0}
        flips: list[CoinSide] = []
        winning_side: CoinSide | None = None
        for flip_index in range(target * 2 - 1):
            result = self._rng.resolve_coin(
                game.server_seed, game.client_seed, game.nonce, flip_index
            )
            flips.append(result)
            scores[result.value] += 1
            if scores[result.value] >= target:
                winning_side = result
                break
        if winning_side is None:
            winning_side = (
                CoinSide.HEADS
                if scores[CoinSide.HEADS.value] > scores[CoinSide.TAILS.value]
                else CoinSide.TAILS
            )
        game.coin_flips = flips
        game.scores = scores
        game.winner_side = winning_side
        game.coin_result = winning_side
        game.winner_ids = list(game.ids_for(winning_side))
        game.winner_id = game.winner_ids[0] if game.winner_ids else None
        game.status = GameStatus.ACTIVE
        game.auto_flip_at = None
        replaced = await self._games.replace(game, session=session)
        if not replaced:
            raise GameError("The game could not be resolved due to a concurrent update.")
        return game

    async def finalize_coinflip(self, game_id: str) -> GameRecord:
        game = await self.get(game_id)
        if game.status is GameStatus.COMPLETED:
            return game
        if not game.is_awaiting_settlement():
            raise GameError("This game is not waiting to settle.")
        if game.winner_side is None:
            raise GameError("This game has no result to settle.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=game.creator_id, gameId=game_id):
            async with self._uow.begin() as uow:
                current = await self._games.find_by_id(game_id, session=uow.session)
                if current is None:
                    raise NotFoundError("That game was not found.")
                if current.status is GameStatus.COMPLETED:
                    return current
                if not current.is_awaiting_settlement() or current.winner_side is None:
                    raise GameError("This game is not waiting to settle.")
                await self._payout_winners(
                    current, list(current.ids_for(current.winner_side)), usd_price, session=uow.session
                )
                replaced = await self._games.replace(current, session=uow.session)
                if not replaced:
                    raise GameError("The game could not be settled due to a concurrent update.")
        return current

    async def _refund_open_game(
        self, game: GameRecord, status: GameStatus, description: str
    ) -> GameRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=game.creator_id, gameId=game.game_id):
            async with self._uow.begin() as uow:
                cancelled = await self._games.cancel_if_pending(
                    game.game_id, status, self._clock.now(), session=uow.session
                )
                if cancelled is None:
                    current = await self._games.find_by_id(game.game_id, session=uow.session)
                    if current and current.status in {GameStatus.CANCELLED, GameStatus.EXPIRED}:
                        return current
                    raise GameError("This game can no longer be refunded.")
                for player_id in cancelled.human_player_ids():
                    mutation = await self._balances.credit(
                        user_id=player_id,
                        amount=cancelled.wager_for(player_id),
                        entry_type=LedgerEntryType.REFUND,
                        transaction_type=TransactionType.REFUND,
                        price_usd=usd_price,
                        description=f"{description} {cancelled.game_id}",
                        game_id=cancelled.game_id,
                        session=uow.session,
                    )
                    cancelled.ledger_ids.append(mutation.ledger_entry.entry_id)
                cancelled = await self._refund_house_seats(
                    cancelled, usd_price, description, session=uow.session
                )
                await self._games.replace(cancelled, session=uow.session)
            self._logger.info("game_refunded", status=status.value)
        return cancelled

    async def _refund_house_seats(self, game: GameRecord, usd_price: Decimal, description: str, *, session) -> GameRecord:
        bet = SolAmount.from_lamports(game.bet_amount_lamports)
        for _seat in game.house_seat_ids:
            _treasury, house_refund = await self._balances.credit_house_reserve(
                amount=bet,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"{description} house {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(house_refund.entry_id)
        return game

    async def _house_table_cap(self, reserved_lamports: int = 0) -> int:
        treasury = await self._house.get()
        return house_table_cap_lamports(
            treasury.sol_balance_lamports,
            reserved_lamports=reserved_lamports,
        )

    async def _require_vs_house_bet_cap(self, bet: SolAmount) -> None:
        cap = await self._house_table_cap()
        if bet.lamports <= cap:
            return
        raise GameError("Max bet is 10% of the house book.")

    async def _require_call_bot_cap(self, game: GameRecord) -> None:
        reserved = game.house_seat_count() * game.bet_amount_lamports
        cap = await self._house_table_cap(reserved)
        if game.can_call_house_seat(max_house=cap, seat=game.bet_amount_lamports):
            return
        raise GameError("The house can take at most 10% of the house book against the bot on this table.")

    async def blackjack_action(
        self,
        game_id: str,
        player_id: str,
        action: BlackjackAction | str,
    ) -> GameRecord:
        parsed = action if isinstance(action, BlackjackAction) else BlackjackAction(str(action).lower())
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                game = await self._games.find_by_id(game_id, session=uow.session)
                if game is None:
                    raise NotFoundError("That game was not found.")
                if not game.is_blackjack() or game.status is not GameStatus.ACTIVE:
                    raise GameError("This blackjack table is not in play.")
                if player_id != game.creator_id:
                    raise GameError("Only the player can act on this table.")
                if parsed not in game.blackjack_legal_actions():
                    raise GameError("That action is not available.")
                await self._apply_blackjack_action(game, parsed, usd_price, session=uow.session)
                if player_finished(game.blackjack_player_hands):
                    await self._complete_blackjack(game, usd_price, session=uow.session)
                replaced = await self._games.replace(game, session=uow.session)
                if not replaced:
                    raise GameError("The hand could not be updated due to a concurrent action.")
            self._logger.info("blackjack_action", action=parsed.value, status=game.status.value)
        return game

    async def _seat_house_and_deal_blackjack(
        self,
        game: GameRecord,
        bet: SolAmount,
        usd_price: Decimal,
        bet_usd: Decimal,
        *,
        session,
    ) -> None:
        await self._require_blackjack_extra_cap(game, extra_lamports=bet.lamports)
        try:
            _treasury, house_bet = await self._balances.debit_house_reserve(
                amount=bet,
                price_usd=usd_price,
                description=f"House blackjack wager {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
        except InsufficientHouseReserveError as exc:
            await self._balances.credit(
                user_id=game.creator_id,
                amount=bet,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"House blackjack refund {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.status = GameStatus.CANCELLED
            game.resolved_at = self._clock.now()
            await self._games.replace(game, session=session)
            raise GameError("The house book does not have enough SOL for this table.") from exc
        game.ledger_ids.append(house_bet.entry_id)
        game.house_seat_ids = [HOUSE_USER_ID]
        game.record_wager(HOUSE_USER_ID, bet.lamports)
        game.ready_ids = [game.creator_id]
        game.blackjack_player_hands = [BlackjackHand(bet_lamports=bet.lamports)]
        player_hand = game.blackjack_player_hands[0]
        player_hand.cards.append(self._draw_blackjack_card(game))
        game.blackjack_dealer_cards.append(self._draw_blackjack_card(game))
        player_hand.cards.append(self._draw_blackjack_card(game))
        game.blackjack_dealer_cards.append(self._draw_blackjack_card(game))
        if player_hand.is_blackjack():
            player_hand.stood = True
            await self._complete_blackjack(game, usd_price, session=session)
            return
        game.status = GameStatus.ACTIVE

    def _draw_blackjack_card(self, game: GameRecord):
        if not game.server_seed:
            raise GameError("This game is missing its provably fair seed.")
        fair = self._rng.resolve_card(
            game.server_seed, game.client_seed, game.nonce, game.blackjack_card_index
        )
        game.blackjack_card_index += 1
        return fair.card

    async def _apply_blackjack_action(
        self,
        game: GameRecord,
        action: BlackjackAction,
        usd_price: Decimal,
        *,
        session,
    ) -> None:
        hand = game.current_blackjack_hand()
        if hand is None:
            raise GameError("There is no hand to play.")
        if action is BlackjackAction.HIT:
            hand.cards.append(self._draw_blackjack_card(game))
            if hand.is_done():
                game.blackjack_hand_index = advance_hand_index(
                    game.blackjack_player_hands, game.blackjack_hand_index
                )
            return
        if action is BlackjackAction.STAND:
            hand.stood = True
            game.blackjack_hand_index = advance_hand_index(
                game.blackjack_player_hands, game.blackjack_hand_index
            )
            return
        extra = SolAmount.from_lamports(hand.bet_lamports)
        extra_usd = self._math.sol_to_fiat(extra, usd_price)
        await self._require_blackjack_extra_cap(game, extra_lamports=extra.lamports)
        debit = await self._balances.debit(
            user_id=game.creator_id,
            amount=extra,
            entry_type=LedgerEntryType.BET,
            transaction_type=TransactionType.BET,
            price_usd=usd_price,
            description=f"Blackjack {action.value} {game.game_id}",
            game_id=game.game_id,
            session=session,
        )
        game.ledger_ids.append(debit.ledger_entry.entry_id)
        try:
            _treasury, house_bet = await self._balances.debit_house_reserve(
                amount=extra,
                price_usd=usd_price,
                description=f"House blackjack {action.value} {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
        except InsufficientHouseReserveError as exc:
            await self._balances.credit(
                user_id=game.creator_id,
                amount=extra,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Blackjack {action.value} refund {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            raise GameError("The house book does not have enough SOL for this action.") from exc
        game.ledger_ids.append(house_bet.entry_id)
        game.record_wager(game.creator_id, game.wager_for(game.creator_id).lamports + extra.lamports)
        game.record_wager(HOUSE_USER_ID, game.wager_for(HOUSE_USER_ID).lamports + extra.lamports)
        if action is BlackjackAction.DOUBLE:
            hand.bet_lamports += extra.lamports
            hand.doubled = True
            hand.cards.append(self._draw_blackjack_card(game))
            hand.stood = True
            game.blackjack_hand_index = advance_hand_index(
                game.blackjack_player_hands, game.blackjack_hand_index
            )
            return
        first, second = hand.cards
        aces = first.rank == 0 and second.rank == 0
        left = BlackjackHand(
            cards=[first],
            bet_lamports=hand.bet_lamports,
            from_split=True,
            from_split_aces=aces,
        )
        right = BlackjackHand(
            cards=[second],
            bet_lamports=extra.lamports,
            from_split=True,
            from_split_aces=aces,
        )
        left.cards.append(self._draw_blackjack_card(game))
        right.cards.append(self._draw_blackjack_card(game))
        game.blackjack_player_hands = [left, right]
        game.blackjack_hand_index = advance_hand_index(game.blackjack_player_hands, 0)

    async def _complete_blackjack(self, game: GameRecord, usd_price: Decimal, *, session) -> None:
        game.blackjack_hole_hidden = False
        needs_dealer = any(
            not hand.is_bust() and not hand.is_blackjack() for hand in game.blackjack_player_hands
        )
        if needs_dealer:
            while dealer_should_hit(game.blackjack_dealer_cards):
                game.blackjack_dealer_cards.append(self._draw_blackjack_card(game))
        collected = SolAmount.zero()
        player_payout = SolAmount.zero()
        player_won_any = False
        for hand in game.blackjack_player_hands:
            hand.outcome = compare_hands(
                hand.cards,
                game.blackjack_dealer_cards,
                from_split=hand.from_split,
            )
            fee, payout = await self._settle_blackjack_hand(
                game, hand, usd_price, session=session
            )
            collected = SolAmount.from_lamports(collected.lamports + fee.lamports)
            player_payout = SolAmount.from_lamports(player_payout.lamports + payout.lamports)
            if hand.outcome in {BlackjackOutcome.WIN, BlackjackOutcome.BLACKJACK}:
                player_won_any = True
        stake = game.wager_for(game.creator_id)
        player_net = player_payout.lamports - stake.lamports
        if player_net > 0:
            game.winner_ids = [game.creator_id]
        elif player_net < 0:
            game.winner_ids = [HOUSE_USER_ID]
        else:
            game.winner_ids = []
        game.winner_id = game.winner_ids[0] if game.winner_ids else None
        now = self._clock.now()
        await self._assign_game_number(game, session=session)
        bet_usd = self._math.sol_to_fiat(stake, usd_price)
        payout_usd = self._math.sol_to_fiat(player_payout, usd_price)
        await self._users.apply_game_stats(
            game.creator_id,
            wagered=stake,
            wagered_usd=bet_usd,
            max_bet_usd=bet_usd,
            fees_paid=collected,
            won=player_won_any,
            won_usd=payout_usd if player_won_any else Decimal("0"),
            now=now,
            session=session,
        )
        if not collected.is_zero() and player_won_any:
            affiliate_ids = await self._affiliates.settle_game(
                game,
                collected_fee=collected,
                price_usd=usd_price,
                winners=[game.creator_id],
                session=session,
            )
            game.ledger_ids.extend(affiliate_ids)
        game.status = GameStatus.COMPLETED
        game.house_fee_lamports = collected.lamports
        game.house_fee_usd = self._math.sol_to_fiat(collected, usd_price)
        game.winner_payout_lamports = player_payout.lamports
        game.per_winner_payout_lamports = player_payout.lamports
        game.resolved_at = now
        game.expires_at = None
        self._logger.info(
            "game_resolved",
            winnerIds=game.winner_ids,
            houseFeeLamports=collected.lamports,
            payoutLamports=player_payout.lamports,
            gameType=game.game_type.value,
        )

    async def _settle_blackjack_hand(
        self,
        game: GameRecord,
        hand: BlackjackHand,
        usd_price: Decimal,
        *,
        session,
    ) -> tuple[SolAmount, SolAmount]:
        wager = SolAmount.from_lamports(hand.bet_lamports)
        if hand.outcome is BlackjackOutcome.PUSH:
            refund = await self._balances.credit(
                user_id=game.creator_id,
                amount=wager,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Blackjack push {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(refund.ledger_entry.entry_id)
            _treasury, house_refund = await self._balances.credit_house_reserve(
                amount=wager,
                entry_type=LedgerEntryType.REFUND,
                transaction_type=TransactionType.REFUND,
                price_usd=usd_price,
                description=f"Blackjack push house {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(house_refund.entry_id)
            return SolAmount.zero(), wager
        if hand.outcome is BlackjackOutcome.BLACKJACK:
            extra = SolAmount.from_lamports(wager.lamports // 2)
            if not extra.is_zero():
                try:
                    _treasury, bonus = await self._balances.debit_house_reserve(
                        amount=extra,
                        price_usd=usd_price,
                        description=f"House blackjack 3:2 {game.game_id}",
                        game_id=game.game_id,
                        session=session,
                    )
                except InsufficientHouseReserveError as exc:
                    raise GameError("The house book does not have enough SOL to pay blackjack 3:2.") from exc
                game.ledger_ids.append(bonus.entry_id)
                game.record_wager(
                    HOUSE_USER_ID, game.wager_for(HOUSE_USER_ID).lamports + extra.lamports
                )
            payout = SolAmount.from_lamports(wager.lamports * 2 + extra.lamports)
        else:
            payout = SolAmount.from_lamports(wager.lamports * 2)
        if hand.outcome is BlackjackOutcome.LOSE:
            _treasury, win_entry = await self._balances.credit_house_reserve(
                amount=SolAmount.from_lamports(wager.lamports * 2),
                entry_type=LedgerEntryType.WIN,
                transaction_type=TransactionType.WIN,
                price_usd=usd_price,
                description=f"Blackjack win {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(win_entry.entry_id)
            return SolAmount.zero(), SolAmount.zero()
        win = await self._balances.credit(
            user_id=game.creator_id,
            amount=payout,
            entry_type=LedgerEntryType.WIN,
            transaction_type=TransactionType.WIN,
            price_usd=usd_price,
            description=f"Blackjack win {game.game_id}",
            game_id=game.game_id,
            won=payout,
            session=session,
        )
        game.ledger_ids.append(win.ledger_entry.entry_id)
        return SolAmount.zero(), payout

    async def _play_limbo(
        self,
        game: GameRecord,
        bet: SolAmount,
        usd_price: Decimal,
        bet_usd: Decimal,
        *,
        session,
    ) -> None:
        if game.limbo_target is None:
            raise GameError("Choose a multiplier from 1.01 to 1,000,000.")
        target = parse_limbo_multiplier(game.limbo_target)
        profit = SolAmount.from_lamports(limbo_profit_lamports(bet.lamports, target))
        try:
            _treasury, house_bet = await self._balances.debit_house_reserve(
                amount=profit,
                price_usd=usd_price,
                description=f"House limbo {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
        except InsufficientHouseReserveError as exc:
            raise GameError("The house book does not have enough SOL for that multiplier.") from exc
        game.ledger_ids.append(house_bet.entry_id)
        game.house_seat_ids = [HOUSE_USER_ID]
        game.record_wager(HOUSE_USER_ID, profit.lamports)
        if not game.server_seed:
            raise GameError("This game is missing its server seed.")
        fair = self._rng.resolve_limbo(game.server_seed, game.client_seed, game.nonce)
        game.limbo_result = fair.crash
        won = fair.crash >= target
        payout = SolAmount.from_lamports(limbo_payout_lamports(bet.lamports, target) if won else 0)
        if won:
            win = await self._balances.credit(
                user_id=game.creator_id,
                amount=SolAmount.from_lamports(limbo_payout_lamports(bet.lamports, target)),
                entry_type=LedgerEntryType.WIN,
                transaction_type=TransactionType.WIN,
                price_usd=usd_price,
                description=f"Limbo win {game.game_id}",
                game_id=game.game_id,
                won=SolAmount.from_lamports(limbo_payout_lamports(bet.lamports, target)),
                session=session,
            )
            game.ledger_ids.append(win.ledger_entry.entry_id)
            game.winner_ids = [game.creator_id]
            player_payout = SolAmount.from_lamports(limbo_payout_lamports(bet.lamports, target))
        else:
            _treasury, take = await self._balances.credit_house_reserve(
                amount=SolAmount.from_lamports(profit.lamports + bet.lamports),
                entry_type=LedgerEntryType.WIN,
                transaction_type=TransactionType.WIN,
                price_usd=usd_price,
                description=f"Limbo house {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(take.entry_id)
            game.winner_ids = [HOUSE_USER_ID]
            player_payout = SolAmount.zero()
        game.winner_id = game.winner_ids[0]
        now = self._clock.now()
        await self._assign_game_number(game, session=session)
        payout_usd = self._math.sol_to_fiat(player_payout, usd_price)
        await self._users.apply_game_stats(
            game.creator_id,
            wagered=bet,
            wagered_usd=bet_usd,
            max_bet_usd=bet_usd,
            fees_paid=SolAmount.zero(),
            won=won,
            won_usd=payout_usd if won else Decimal("0"),
            now=now,
            session=session,
        )
        game.status = GameStatus.COMPLETED
        game.house_fee_lamports = 0
        game.house_fee_usd = Decimal("0")
        game.winner_payout_lamports = player_payout.lamports
        game.per_winner_payout_lamports = player_payout.lamports
        game.resolved_at = now
        game.expires_at = None
        game.auto_flip_at = None
        self._logger.info(
            "game_resolved",
            winnerIds=game.winner_ids,
            houseFeeLamports=0,
            payoutLamports=player_payout.lamports,
            gameType=game.game_type.value,
            limboTarget=str(target),
            limboResult=str(fair.crash),
        )

    async def _require_blackjack_extra_cap(self, game: GameRecord, *, extra_lamports: int) -> None:
        reserved = int(game.player_wager_lamports.get(HOUSE_USER_ID, 0))
        cap = await self._house_table_cap(reserved)
        if reserved + extra_lamports <= cap:
            return
        raise GameError("The house can take at most 10% of the house book on this table.")

    async def mines_reveal(self, game_id: str, player_id: str, tile: int) -> GameRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                game = await self._require_active_mines(game_id, player_id, session=uow.session)
                index = int(tile)
                if index < 0 or index >= MINES_TILES:
                    raise GameError("Pick a tile on the board.")
                if index in game.mines_revealed:
                    raise GameError("That tile is already open.")
                if index in game.mines_tiles:
                    game.mines_revealed.append(index)
                    game.mines_hit = index
                    await self._complete_mines(game, usd_price, session=uow.session)
                else:
                    extra = self._mines_profit_delta(
                        game.bet_amount_lamports,
                        game.mines_count,
                        len(game.mines_revealed),
                        len(game.mines_revealed) + 1,
                    )
                    if extra > 0:
                        try:
                            _treasury, house_bet = await self._balances.debit_house_reserve(
                                amount=SolAmount.from_lamports(extra),
                                price_usd=usd_price,
                                description=f"House mines {game.game_id}",
                                game_id=game.game_id,
                                session=uow.session,
                            )
                        except InsufficientHouseReserveError as exc:
                            raise GameError(
                                "The house book does not have enough SOL for that tile."
                            ) from exc
                        game.ledger_ids.append(house_bet.entry_id)
                        game.record_wager(
                            HOUSE_USER_ID,
                            game.wager_for(HOUSE_USER_ID).lamports + extra,
                        )
                    game.mines_revealed.append(index)
                    if len(game.mines_revealed) >= mines_gems_available(game.mines_count):
                        await self._complete_mines(game, usd_price, session=uow.session)
                replaced = await self._games.replace(game, session=uow.session)
                if not replaced:
                    raise GameError("The board could not be updated due to a concurrent action.")
            self._logger.info("mines_reveal", tile=index, status=game.status.value)
        return game

    async def mines_cashout(self, game_id: str, player_id: str) -> GameRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        with LogContext.bind(userId=player_id, gameId=game_id):
            async with self._uow.begin() as uow:
                game = await self._require_active_mines(game_id, player_id, session=uow.session)
                if not game.mines_revealed:
                    raise GameError("Open at least one gem before cashing out.")
                await self._complete_mines(game, usd_price, session=uow.session)
                replaced = await self._games.replace(game, session=uow.session)
                if not replaced:
                    raise GameError("The board could not be updated due to a concurrent action.")
            self._logger.info("mines_cashout", gems=len(game.mines_revealed))
        return game

    async def _require_active_mines(self, game_id: str, player_id: str, *, session) -> GameRecord:
        game = await self._games.find_by_id(game_id, session=session)
        if game is None:
            raise NotFoundError("That game was not found.")
        if not game.is_mines() or game.status is not GameStatus.ACTIVE:
            raise GameError("This mines table is not in play.")
        if player_id != game.creator_id:
            raise GameError("Only the player can act on this table.")
        return game

    def _mines_profit_delta(self, bet_lamports: int, mines: int, old_gems: int, new_gems: int) -> int:
        old_profit = max(mines_payout_lamports(bet_lamports, mines, old_gems) - bet_lamports, 0)
        new_profit = max(mines_payout_lamports(bet_lamports, mines, new_gems) - bet_lamports, 0)
        return max(new_profit - old_profit, 0)

    async def _start_mines(self, game: GameRecord, bet: SolAmount, usd_price: Decimal, *, session) -> None:
        mines = parse_mines_count(game.mines_count)
        if not game.server_seed:
            raise GameError("This game is missing its server seed.")
        fair = self._rng.resolve_mines(game.server_seed, game.client_seed, game.nonce, mines)
        game.house_seat_ids = [HOUSE_USER_ID]
        game.record_wager(HOUSE_USER_ID, 0)
        game.mines_tiles = list(fair.mines)
        game.mines_revealed = []
        game.mines_hit = None
        game.ready_ids = [game.creator_id]
        game.status = GameStatus.ACTIVE
        game.auto_flip_at = None

    async def _complete_mines(self, game: GameRecord, usd_price: Decimal, *, session) -> None:
        bet = SolAmount.from_lamports(game.bet_amount_lamports)
        reserved = SolAmount.from_lamports(int(game.player_wager_lamports.get(HOUSE_USER_ID, 0)))
        gems = len(game.mines_revealed)
        hit = game.mines_hit is not None
        payout = (
            SolAmount.zero()
            if hit
            else SolAmount.from_lamports(mines_payout_lamports(bet.lamports, game.mines_count, gems))
        )
        leftover = SolAmount.from_lamports(max(reserved.lamports + bet.lamports - payout.lamports, 0))
        if not leftover.is_zero():
            _treasury, take = await self._balances.credit_house_reserve(
                amount=leftover,
                entry_type=LedgerEntryType.WIN,
                transaction_type=TransactionType.WIN,
                price_usd=usd_price,
                description=f"Mines house {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            game.ledger_ids.append(take.entry_id)
        if not payout.is_zero():
            win = await self._balances.credit(
                user_id=game.creator_id,
                amount=payout,
                entry_type=LedgerEntryType.WIN,
                transaction_type=TransactionType.WIN,
                price_usd=usd_price,
                description=f"Mines win {game.game_id}",
                game_id=game.game_id,
                won=payout,
                session=session,
            )
            game.ledger_ids.append(win.ledger_entry.entry_id)
            game.winner_ids = [game.creator_id]
        else:
            game.winner_ids = [HOUSE_USER_ID]
        game.winner_id = game.winner_ids[0]
        now = self._clock.now()
        await self._assign_game_number(game, session=session)
        bet_usd = game.stake_usd() if game.bet_amount_usd > 0 else self._math.sol_to_fiat(bet, usd_price)
        payout_usd = self._math.sol_to_fiat(payout, usd_price)
        won = not hit
        await self._users.apply_game_stats(
            game.creator_id,
            wagered=bet,
            wagered_usd=bet_usd,
            max_bet_usd=bet_usd,
            fees_paid=SolAmount.zero(),
            won=won,
            won_usd=payout_usd if won else Decimal("0"),
            now=now,
            session=session,
        )
        game.status = GameStatus.COMPLETED
        game.house_fee_lamports = 0
        game.house_fee_usd = Decimal("0")
        game.winner_payout_lamports = payout.lamports
        game.per_winner_payout_lamports = payout.lamports
        game.resolved_at = now
        game.expires_at = None
        game.auto_flip_at = None
        self._logger.info(
            "game_resolved",
            winnerIds=game.winner_ids,
            houseFeeLamports=0,
            payoutLamports=payout.lamports,
            gameType=game.game_type.value,
            minesCount=game.mines_count,
            minesGems=gems,
            minesHit=game.mines_hit,
        )
