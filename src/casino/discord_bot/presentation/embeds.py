from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.builder import CasinoPanel, EmbedBuilder
from casino.discord_bot.presentation.ids import ComponentIdCodec
from casino.domain.affiliate import (
    AFFILIATE_TIERS,
    AffiliateStats,
    format_affiliate_rate,
    next_affiliate_tier,
)
from casino.domain.blackjack import BlackjackAction, BlackjackOutcome, hand_total
from casino.domain.limbo import LIMBO_START, format_limbo_multiplier
from casino.domain.mines import MINES_TILES, format_mines_multiplier, mines_multiplier
from casino.domain.rain import RAIN_JOIN_STATUS, RainJoinEligibility
from casino.domain.investor import InvestorPosition
from casino.domain.enums import (
    CoinSide,
    GameMode,
    GameStatus,
    GameType,
    JackpotStatus,
    RainStatus,
    TicketCategory,
    TicketStatus,
)
from casino.domain.records import (
    DiceRollRecord,
    DiceTurnEvent,
    GameRecord,
    GuildConfigRecord,
    HouseStatsReport,
    JackpotRecord,
    PlayerProfileStats,
    RainRecord,
    TicketRecord,
    TimeframeStats,
    UserRecord,
    is_house_seat,
)
from casino.services.price_service import PriceQuote
from casino.utils.money import SolAmount

_FIAT_CENTS = Decimal("0.01")


class EmbedFactory:
    """All player-facing panels go through EmbedBuilder so style stays uniform."""

    def __init__(self, container: AppContainer) -> None:
        self._container = container
        self._ids = ComponentIdCodec()

    def builder(self, title: str, *, accent: int | None = None, description: str = "") -> EmbedBuilder:
        return EmbedBuilder().title(title).accent(accent).description(description)

    def error(self, message: str) -> CasinoPanel:
        return self.builder("Action failed", description=message).build()

    def success(self, title: str, message: str) -> CasinoPanel:
        return self.builder(title, description=message).build()

    def tip_received(
        self,
        sender_id: str,
        amount: SolAmount,
        *,
        fiat: Decimal,
        currency: str,
    ) -> CasinoPanel:
        math = self._container.math
        return self.builder(
            "Tip received",
            description=(
                f"{self._mention(sender_id)} sent you "
                f"**{math.format_fiat_symbol(fiat, currency)}** "
                f"(`{math.format_sol(amount)} SOL`)."
            ),
        ).build()

    def house_balance(self, reserves_lamports: int, quote: PriceQuote) -> CasinoPanel:
        math = self._container.math
        house = SolAmount.from_lamports(reserves_lamports)
        usd = math.sol_to_fiat(house, quote.sol_price("USD"))
        address = self._container.deposits.house_address()
        builder = (
            self.builder("House Balance")
            .field("", f"**{math.format_fiat_symbol(usd, 'USD')}** (`{math.format_sol(house)} SOL`)")
        )
        if address:
            builder.button("View on blockchain", url=f"https://solscan.io/account/{address}")
        return builder.build()

    def balance(self, user: UserRecord, quote: PriceQuote) -> CasinoPanel:
        math = self._container.math
        currency = user.display_currency
        sol = user.sol_balance
        fiat = math.sol_to_fiat(sol, quote.sol_price(currency))
        usd = math.sol_to_fiat(sol, quote.sol_price("USD"))
        stale = " (price cache)" if quote.stale else ""
        builder = (
            self.builder("Your balance")
            .field("SOL", f"`{math.format_sol(sol)} SOL`")
            .field(currency, f"`{math.format_fiat_symbol(fiat, currency)}`{stale}")
            .footer("Deposits credit after confirmation on your personal address.")
            .button("Deposit", style=discord.ButtonStyle.success, custom_id=self._ids.DEPOSIT)
            .button("Withdraw", style=discord.ButtonStyle.secondary, custom_id=self._ids.WITHDRAW)
        )
        if currency != "USD":
            builder.field("USD", f"`{math.format_fiat_symbol(usd, 'USD')}`")
        return builder.build()

    def deposits_unavailable(self) -> CasinoPanel:
        return self.builder(
            "Deposits unavailable",
            description=(
                "On-chain deposits need a deposit master secret and a super wallet. "
                "Coinflip still works with developer-credited balances. "
                "Generate them with `python -m casino.tools.generate_deposit_master` and "
                "`python -m casino.tools.generate_super_wallet`."
            ),
        ).build()

    def deposit(self, user: UserRecord) -> CasinoPanel:
        address = user.deposit_address or "not ready"
        return (
            self.builder(
                "Deposit",
                description=(
                    "Send **native SOL** to your personal deposit address. It stays the same for you. The full amount is credited — no deposit fee."
                ),
            )
            .field("Deposit Address", f"`{address}`")
            .build()
        )

    def open_game(self, game: GameRecord) -> CasinoPanel:
        if game.is_blackjack():
            return self.blackjack_table(game)
        if game.is_limbo():
            return self.limbo_table(game)
        if game.is_mines():
            return self.mines_table(game)
        if game.is_target():
            return self.target_table(game)
        mode_label = self._mode_label(game)
        started = "dice table" if game.is_dice() else "coinflip"
        builder = (
            self.builder(
                f"{game.game_type.value.capitalize()} · {mode_label}",
                description=f"{self._mention(game.creator_id)} started a new {started}.",
            )
            .persistent()
        )
        if game.is_dice_team():
            self._add_dice_teams(builder, game)
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        elif game.is_dice():
            builder.field("Players", self._players_with_sides(game))
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        else:
            self._add_coinflip_sides(builder, game)
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        self._add_listing_actions(builder, game)
        return builder.build()

    def blackjack_table(
        self,
        game: GameRecord,
        *,
        dealer_cards: list | None = None,
        show_result: bool | None = None,
        balance_text: str = "",
        actions: bool = True,
        history: bool = False,
    ) -> CasinoPanel:
        allowed = game.blackjack_legal_actions()
        playing = game.status is GameStatus.ACTIVE
        if show_result is None:
            show_result = game.status is GameStatus.COMPLETED
        title = "Blackjack"
        if history and show_result:
            title = self._vs_house_title(game, "Blackjack")
        builder = (
            self.builder(title, description=balance_text)
            .persistent()
            .footer("Blackjack pays 3:2 · Dealer hits on soft 17")
            .field("Dealer", self._blackjack_dealer_line(game, cards=dealer_cards))
            .divider()
            .field(self._mention(game.creator_id), self._blackjack_player_lines(game, show_outcome=show_result))
            .divider()
            .field("Wager", self._wager_usd(game))
            .divider()
        )
        if not actions:
            if show_result:
                builder.button(
                    "Verify",
                    style=discord.ButtonStyle.secondary,
                    custom_id=self._ids.verify_game(game.game_id),
                )
            return builder.build()
        if playing:
            builder = (
                builder.button(
                    "Hit",
                    style=discord.ButtonStyle.success,
                    custom_id=self._ids.blackjack_hit(game.game_id),
                    disabled=BlackjackAction.HIT not in allowed,
                )
                .button(
                    "Stand",
                    style=discord.ButtonStyle.danger,
                    custom_id=self._ids.blackjack_stand(game.game_id),
                    disabled=BlackjackAction.STAND not in allowed,
                )
                .button(
                    "Split",
                    style=discord.ButtonStyle.secondary,
                    custom_id=self._ids.blackjack_split(game.game_id),
                    disabled=BlackjackAction.SPLIT not in allowed,
                )
                .button(
                    "Double",
                    style=discord.ButtonStyle.primary,
                    custom_id=self._ids.blackjack_double(game.game_id),
                    disabled=BlackjackAction.DOUBLE not in allowed,
                )
            )
        elif show_result:
            builder.button(
                "Repeat",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.blackjack_new(game.game_id),
            ).button(
                "Change Bet",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.blackjack_bet(game.game_id),
            ).button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        return builder.build()

    async def blackjack_panel(
        self,
        game: GameRecord,
        *,
        dealer_cards: list | None = None,
        show_result: bool | None = None,
    ) -> CasinoPanel:
        conceal = show_result is False and game.status is GameStatus.COMPLETED
        return self.blackjack_table(
            game,
            dealer_cards=dealer_cards,
            show_result=show_result,
            balance_text=await self._blackjack_balance_line(game, conceal_payout=conceal),
        )

    def limbo_table(
        self,
        game: GameRecord,
        *,
        actions: bool = True,
        crash_value: Decimal | None = None,
        reveal: bool = True,
        balance_text: str = "",
        history: bool = False,
    ) -> CasinoPanel:
        shown = crash_value if crash_value is not None else game.limbo_result
        if shown is None:
            shown = LIMBO_START
        crash_text = format_limbo_multiplier(shown)
        target = format_limbo_multiplier(game.limbo_target) if game.limbo_target is not None else "—"
        lines = [f"**{crash_text}x**"]
        finished = reveal and game.status is GameStatus.COMPLETED
        if finished:
            change = self._player_balance_change(game, game.creator_id)
            if game.winner_ids == [game.creator_id]:
                outcome = f"{self._mention(game.creator_id)} hit **{target}x**."
            else:
                outcome = f"{self._mention(game.creator_id)} missed **{target}x**."
            lines.append(f"{outcome}  {change}")
        if balance_text:
            lines.append(balance_text)
        title = self._vs_house_title(game, "Limbo") if history and finished else "Limbo"
        builder = (
            self.builder(title, description="\n".join(lines))
            .persistent()
            .field("Wager", self._wager_usd(game))
            .divider()
        )
        if actions and finished:
            builder.button(
                "New Round",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.limbo_new(game.game_id),
            ).button(
                "Change Bet",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.limbo_bet(game.game_id),
            ).button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        elif actions is False and finished:
            builder.button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        return builder.build()

    async def mines_panel(self, game: GameRecord) -> CasinoPanel:
        return self.mines_table(game, balance_text=await self._blackjack_balance_line(game))

    def mines_table(
        self,
        game: GameRecord,
        *,
        actions: bool = True,
        balance_text: str = "",
        history: bool = False,
    ) -> CasinoPanel:
        gems = len(game.mines_revealed)
        playing = game.status is GameStatus.ACTIVE
        finished = game.status is GameStatus.COMPLETED
        current = format_mines_multiplier(mines_multiplier(game.mines_count, gems))
        lines = [f"{self._mention(game.creator_id)} · **{game.mines_count}** mines · **{current}x**"]
        if finished:
            change = self._player_balance_change(game, game.creator_id)
            if game.mines_hit is not None:
                lines.append(f"Hit a mine on tile **{game.mines_hit + 1}**.  {change}")
            else:
                lines.append(f"Cashed out **{gems}** gem{'s' if gems != 1 else ''}.  {change}")
        if balance_text:
            lines.append(balance_text)
        title = self._vs_house_title(game, "Mines") if history and finished else "Mines"
        builder = self.builder(title, description="\n".join(lines)).persistent()
        mine_set = set(game.mines_tiles)
        revealed = set(game.mines_revealed)
        for index in range(MINES_TILES):
            builder.button(
                **self._mines_tile_button(game, index, mine_set=mine_set, revealed=revealed, playing=playing)
            )
        builder.field("Wager", self._wager_usd(game)).divider()
        if actions and playing:
            builder.button(
                "Cash out",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.mines_cashout(game.game_id),
                disabled=not game.mines_revealed,
            )
        elif actions and finished:
            builder.button(
                "New Round",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.mines_new(game.game_id),
            ).button(
                "Change Bet",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.mines_bet(game.game_id),
            ).button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        elif actions is False and finished:
            builder.button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        return builder.build()

    def _mines_tile_button(
        self,
        game: GameRecord,
        index: int,
        *,
        mine_set: set[int],
        revealed: set[int],
        playing: bool,
    ) -> dict[str, object]:
        opened = index in revealed
        is_mine = index in mine_set
        show_mines = game.status is GameStatus.COMPLETED
        if opened and is_mine:
            label, style = "💣", discord.ButtonStyle.danger
        elif opened:
            label, style = "💎", discord.ButtonStyle.success
        elif show_mines and is_mine:
            label, style = "💣", discord.ButtonStyle.danger
        else:
            label, style = "\u200b", discord.ButtonStyle.secondary
        return {
            "label": label,
            "style": style,
            "custom_id": self._ids.mines_tile(game.game_id, index),
            "disabled": (not playing) or opened,
        }

    def jackpot_table(self, jackpot: JackpotRecord, *, history: bool = False) -> CasinoPanel:
        from casino.domain.jackpot import max_join_lamports, win_chance

        pot = jackpot.pot_lamports()
        showing = jackpot.status is JackpotStatus.SHOWING or history
        if showing and jackpot.winner_id:
            description = (
                f"{self._mention(jackpot.winner_id)} won the jackpot and took "
                f"**{self._lamports_usd(jackpot.winner_payout_lamports)}**."
            )
        elif showing:
            description = "Not enough players. Bets were returned."
        elif jackpot.opener_id:
            description = (
                "Join with any amount up to **10×** the first player's bet. "
                "Chance is **your bet / the pot**. Winner takes the pot after 60 seconds."
            )
        else:
            description = (
                "Be the first to join with any amount of your balance. "
                "Later joins can be any amount up to **10×** that first bet. "
                "The draw is 60 seconds after the first join. Chance is **your bet / the pot**."
            )
        builder = self.builder("Jackpot" if not history else "Jackpot result", description=description).persistent()
        builder.field("Pot", self._lamports_usd(pot) if pot else "—")
        if jackpot.status is JackpotStatus.OPEN and jackpot.expires_at is not None:
            builder.field(
                "Draws",
                f"{discord.utils.format_dt(jackpot.expires_at, style='F')} "
                f"({discord.utils.format_dt(jackpot.expires_at, style='R')})",
            )
        opener = jackpot.opener_lamports()
        if jackpot.status is JackpotStatus.OPEN and opener is not None:
            builder.field("Max join", self._lamports_usd(max_join_lamports(opener)))
        players = jackpot.player_ids()
        if players:
            lines: list[str] = []
            extra = 0
            for player_id in players:
                if len(lines) >= 15:
                    extra += 1
                    continue
                bet = int(jackpot.entries_lamports[player_id])
                chance = win_chance(bet, pot) * 100
                marker = " ←" if showing and player_id == jackpot.winner_id else ""
                lines.append(
                    f"{self._mention(player_id)} · {self._lamports_usd(bet)} · {chance:.2f}%{marker}"
                )
            if extra:
                lines.append(f"+{extra} more")
            builder.field("Players", "\n".join(lines))
        else:
            builder.field("Players", "Waiting for the first join.")
        if jackpot.status is JackpotStatus.OPEN and not history:
            builder.button(
                "Join",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.jackpot_join(jackpot.jackpot_id),
            )
        if showing:
            builder.field("Server seed", f"`{jackpot.server_seed or '—'}`")
            builder.field("Client seed", f"`{jackpot.client_seed}`")
            builder.field("Nonce", str(jackpot.nonce))
            if jackpot.ticket is not None:
                builder.field("Ticket", str(jackpot.ticket))
        else:
            builder.field("Server seed hash", f"`{jackpot.server_seed_hash}`")
        return builder.build()

    async def limbo_panel(
        self,
        game: GameRecord,
        *,
        crash_value: Decimal | None = None,
        reveal: bool = True,
    ) -> CasinoPanel:
        return self.limbo_table(
            game,
            crash_value=crash_value,
            reveal=reveal,
            balance_text=await self._blackjack_balance_line(game),
        )

    def target_table(
        self,
        game: GameRecord,
        *,
        draw_number: int | None = None,
        reveal: bool = True,
    ) -> CasinoPanel:
        pending = game.status is GameStatus.PENDING
        counting = game.status is GameStatus.COMPLETED and not reveal
        description = ""
        if game.status is GameStatus.COMPLETED:
            shown = draw_number if draw_number is not None else game.target_number
            description = f"**{shown if shown is not None else '—'}**"
            if not counting:
                description = f"{description}\n{self._target_result_line(game)}"
        builder = (
            self.builder(f"Target · {game.matchup_label()}", description=description)
            .persistent()
            .field("Players", self._target_player_lines(game, reveal=reveal))
            .divider()
            .field("Wager", self._wager_usd(game))
        )
        if pending:
            builder.button(
                "Join",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.join(game.game_id),
                disabled=game.is_full(),
            )
            builder.button(
                "Lock number",
                style=discord.ButtonStyle.primary,
                custom_id=self._ids.target_lock(game.game_id),
            )
            if self._can_show_call_bot(game):
                builder.button(
                    "Call house",
                    style=discord.ButtonStyle.secondary,
                    custom_id=self._ids.call_bot(game.game_id),
                )
            builder.button(
                "Leave",
                style=discord.ButtonStyle.danger,
                custom_id=self._ids.leave(game.game_id),
            )
            builder.button(
                "Cancel",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.cancel(game.game_id),
            )
        else:
            builder.button(
                "Verify",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.verify_game(game.game_id),
            )
        return builder.build()

    def table_overview(
        self,
        game: GameRecord,
        *,
        include_join: bool = False,
        conceal_result: bool = False,
    ) -> CasinoPanel:
        mode_label = self._mode_label(game)
        if conceal_result and not game.is_dice():
            description = "The coin is flipping..."
            if (game.first_to or 1) > 1:
                description = f"First to **{game.first_to}**. {description}"
            builder = (
                self.builder(
                    f"{game.game_type.value.title()} · {mode_label}",
                    description=description,
                )
                .persistent()
            )
            self._add_coinflip_sides(builder, game, conceal_score=True)
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
            self._add_provably_fair_button(builder, game)
            return builder.build()
        if game.is_dice() and game.status is GameStatus.ACTIVE:
            turn = game.current_turn_id()
            description = f"First to **{game.first_to or 1}**. Use `/roll` on your turn."
            if game.is_dice_team():
                description = (
                    f"First to **{game.first_to or 1}**. Highest roll scores a point for that team. "
                    "Use `/roll` on your turn."
                )
            if game.is_dice_roll_off():
                names = ", ".join(self._mention(player_id) for player_id in game.rolling_player_ids())
                description = (
                    f"First to **{game.first_to or 1}**. Tied players roll again for the point: {names}."
                )
            if turn:
                description = f"{description}\nCurrent turn: {self._mention(turn)}"
        elif game.is_dice() and game.status is GameStatus.COMPLETED:
            description = f"{self._winner_line(game)}\nFirst to **{game.first_to or 1}**."
        elif game.is_dice_team():
            description = (
                f"First to **{game.first_to or 1}**. Highest roll scores a point for Team 1 or Team 2. "
                "The game starts when all players are *Ready*, then use `/roll` on your turn."
            )
        elif game.is_dice():
            description = (
                f"First to **{game.first_to or 1}**. The game starts when all players are *Ready*, "
                "then use `/roll` on your turn."
            )
        elif game.status is GameStatus.COMPLETED:
            description = f"{self._winner_line(game)}\nFirst to **{game.first_to or 1}**."
        else:
            description = "The game will begin when all players are *Ready*."
            if (game.first_to or 1) > 1:
                description = f"First to **{game.first_to}**. {description}"
            if include_join:
                description = (
                    "Join here if you have enough balance. "
                    "The game begins when all seated players press *Ready*."
                )
                if (game.first_to or 1) > 1:
                    description = f"First to **{game.first_to}**. {description}"
        builder = (
            self.builder(
                f"{game.game_type.value.title()} · {mode_label}",
                description=description,
            )
            .persistent()
        )
        if game.is_dice_team():
            self._add_dice_teams(builder, game, ready_status=game.status is GameStatus.PENDING)
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        elif game.is_dice():
            builder.field("Players", self._players_with_sides(game, ready_status=game.status is GameStatus.PENDING))
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        else:
            self._add_coinflip_sides(
                builder,
                game,
                ready_status=game.status is GameStatus.PENDING,
                result=game.status is GameStatus.COMPLETED,
            )
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
        if include_join:
            self._add_listing_actions(builder, game)
        self._add_table_actions(builder, game)
        self._add_provably_fair_button(builder, game)
        return builder.build()

    def flip_countdown(self, game: GameRecord, seconds: int, *, flip_index: int = 0) -> CasinoPanel:
        # Keep this as Components V2 (no inline fields). Inline app games edit this
        # same message, and a V2 message cannot be turned into a classic embed.
        target = game.first_to or 1
        if target > 1:
            description = (
                f"Flip **{flip_index + 1}**\n"
                f"{self._coinflip_score_line(game, through=flip_index)}\n"
                f"Flipping in **{seconds}**..."
            )
        else:
            description = f"Flipping in **{seconds}**..."
        return (
            self.builder("Coinflip", description=description)
            .build()
        )

    def flip_reveal(self, game: GameRecord, flip_index: int) -> CasinoPanel:
        flips = game.flip_series()
        side = flips[flip_index] if flip_index < len(flips) else game.coin_result
        landed = (side.value if side else "—").title()
        target = game.first_to or 1
        return (
            self.builder(
                f"Flip {flip_index + 1}",
                description=(
                    f"The coin landed on **{landed}**.\n"
                    f"{self._coinflip_score_line(game, through=flip_index + 1)}\n"
                ),
            )
            .build()
        )

    def resolved_game(self, game: GameRecord, *, rerun: bool = False) -> CasinoPanel:
        if game.is_blackjack():
            return self.blackjack_table(game, actions=False, history=True)
        if game.is_limbo():
            return self.limbo_table(game, actions=False, history=True)
        if game.is_mines():
            return self.mines_table(game, actions=False, history=True)
        if game.is_target():
            return self.target_table(game)
        title = self._game_title(game)
        if game.is_dice():
            description = f"{self._winner_line(game)}\nFirst to **{game.first_to or 1}**."
            if game.is_dice_team():
                builder = self.builder(title, description=description)
                self._add_dice_teams(builder, game, result=True, pad_open=False)
                builder.divider()
                builder.field("Wager", self._wager_usd(game))
                builder.button(
                    "Verify",
                    style=discord.ButtonStyle.secondary,
                    custom_id=self._ids.verify_game(game.game_id),
                )
            else:
                builder = (
                    self.builder(title, description=description)
                    .field("Players", self._players_with_sides(game, result=True))
                    .divider()
                    .field("Wager", self._wager_usd(game))
                    .button("Verify", style=discord.ButtonStyle.secondary, custom_id=self._ids.verify_game(game.game_id))
                )
        else:
            description = f"{self._winner_line(game)}\nFirst to **{game.first_to or 1}**."
            builder = self.builder(title, description=description)
            self._add_coinflip_sides(builder, game, result=True, pad_open=False)
            builder.divider()
            builder.field("Wager", self._wager_usd(game))
            builder.button("Verify", style=discord.ButtonStyle.secondary, custom_id=self._ids.verify_game(game.game_id))
        if rerun:
            builder.button("Rerun", style=discord.ButtonStyle.primary, custom_id=self._ids.rerun(game.game_id))
        return builder.persistent().build()

    def dice_roll(
        self,
        game: GameRecord,
        roll: DiceRollRecord,
        *,
        event: DiceTurnEvent | None = None,
    ) -> CasinoPanel:
        extra = ""
        if event is not None and event.round_winner_id:
            if game.is_dice_team():
                side = game.side_of(event.round_winner_id)
                team = game.team_label(side) if side is not None else "A team"
                extra = f"\n**{team}** wins the round."
            else:
                extra = f"\n{self._mention(event.round_winner_id)} wins the round."
        elif event is not None and event.roll_off_ids:
            names = ", ".join(self._mention(player_id) for player_id in event.roll_off_ids)
            extra = f"\nTied high — only {names} roll again for the point."
        elif event is not None and event.round_tied:
            extra = "\nRound tied — nobody gets a point."
        return (
            self.builder(
                f"Round {roll.round_index}",
                description=(
                    f"{self._mention(roll.player_id)} rolled **{roll.die_a}** and **{roll.die_b}** "
                    f"— total **{roll.total}**.{extra}"
                ),
            )
            .field("Score", self._dice_scoreboard(game, roll.scores))
            .build()
        )

    def rematch_invite(self, game: GameRecord, source: GameRecord) -> CasinoPanel:
        return (
            self.builder(
                "Rematch",
                description=(
                    f"{self._mention(game.creator_id)} wants to run it back with the same rules. "
                    "Other players from the last match can **Accept** or **Decline**."
                ),
            )
            .field("Wager", self._wager_usd(game))
            .field("Players", self._rematch_players(game, source))
            .button("Accept", style=discord.ButtonStyle.success, custom_id=self._ids.accept_rematch(game.game_id))
            .button("Decline", style=discord.ButtonStyle.danger, custom_id=self._ids.decline_rematch(game.game_id))
            .persistent()
            .build()
        )

    def cancelled_game(self, game: GameRecord) -> CasinoPanel:
        status = "expired" if game.status is GameStatus.EXPIRED else "cancelled"
        players = ", ".join(self._mention(player_id) for player_id in game.human_player_ids()) or self._mention(
            game.creator_id
        )
        return self.builder(
            f"{game.game_type.value.title()} {status}",
            description=f"Wagers were refunded to {players}.",
        ).build()

    def provably_fair_hub(self, latest: GameRecord | None = None) -> CasinoPanel:
        builder = self.builder(
            "Provably Fair",
            description=(
                "Blackjack, limbo, and mines lock `SHA256(server_seed)` on your account **before** "
                "the next bet. Your client seed is yours — randomize it in `/settings` any time "
                "before you play. After the round, Verify reveals the server seed so SHA-256 must "
                "match the hash you already had.\n\n"
                "PvP tables commit a fresh server seed when the table is created. "
                "The server seed stays hidden until the game completes.\n\n"
                "Press **Verify** on a result, then paste the values into the verifier.\n"
                "• **Coinflip** — paste the nonce as-is. The site's 0–100 number is the result "
                "(0–50 Heads, 50–100 Tails).\n"
                "• **Dice** — paste nonce as `gameNonce:rollIndex` (first roll is `N:0`). "
                "Ignore the site's 0–100 number; the HMAC hash becomes the two dice.\n"
                "• **Blackjack** — paste nonce as `gameNonce:cardIndex` (first card is `N:0`). "
                "The first 8 hex characters of the HMAC pick a card 0–51.\n"
                "• **Limbo** — HMAC of `clientSeed:nonce:limbo`. First 13 hex characters become the crash.\n"
                "• **Mines** — HMAC of `clientSeed:nonce:mines:drawIndex` plants each mine on the 5×5 board.\n"
                "• **Target** — paste nonce as `gameNonce:target:0` for the draw (1–999). "
                "House picks use `gameNonce:target:1`."
            ),
        )
        return builder.persistent().build()

    def provably_fair(self, game: GameRecord) -> CasinoPanel:
        revealed = game.status is GameStatus.COMPLETED and bool(game.server_seed)
        if game.is_dice():
            return self._dice_fair(game, revealed)
        if game.is_blackjack():
            return self._blackjack_fair(game, revealed)
        if game.is_limbo():
            return self._limbo_fair(game, revealed)
        if game.is_mines():
            return self._mines_fair(game, revealed)
        if game.is_target():
            return self._target_fair(game, revealed)
        return self._coinflip_fair(game, revealed)

    def joined_table(self, game: GameRecord) -> CasinoPanel:
        title = self._game_title(game)
        mode = self._mode_label(game)
        if game.private_channel_id:
            description = (
                f"You joined **{title}** ({mode}).\n"
                f"Open the table: <#{game.private_channel_id}>"
            )
        elif game.is_target():
            description = (
                f"You joined **{title}** ({mode}).\n"
                "Press **Lock number** on the listing and pick 1–999."
            )
        else:
            description = (
                f"You joined **{title}** ({mode}).\n"
                "Press **Ready** on the table embed when you're set."
            )
        return self.builder("Joined table", description=description).build()

    def profile(
        self,
        member: discord.abc.User,
        user: UserRecord,
        stats: PlayerProfileStats,
    ) -> CasinoPanel:
        math = self._container.math
        sign = "+" if stats.profit_usd >= 0 else "-"
        return (
            self.builder(member.display_name)
            .thumbnail(member.display_avatar.url)
            .field("Games", str(stats.games_played))
            .divider()
            .field("Wins", str(stats.games_won))
            .divider()
            .field(
                "Wagered",
                f"{math.format_fiat_symbol(stats.wagered_usd, 'USD')}",
            )
            .divider()
            .field(
                "Profit",
                (
                    f"{sign}{math.format_fiat_symbol(abs(stats.profit_usd), 'USD')} "
                ),
            )
            .divider()
            .button("History", style=discord.ButtonStyle.primary, custom_id=self._ids.history(user.discord_id, 0))
            .build()
        )

    def affiliate(
        self,
        member: discord.abc.User,
        stats: AffiliateStats,
        *,
        just_bound: bool = False,
    ) -> CasinoPanel:
        math = self._container.math
        if stats.referred_by:
            referrer = f"<@{stats.referred_by}> (locked)"
        else:
            referrer = "Not set. Use `/affiliate @user` to lock one in. This cannot be changed."
        description = (
            "You earn a cut of the house game fee when someone you referred wins. "
            "House bet winnings are not shared."
        )
        if just_bound:
            description = f"Referrer locked to <@{stats.referred_by}>.\n\n{description}"
        builder = (
            self.builder("Affiliate", description=description)
            .thumbnail(member.display_avatar.url)
            .field("Your referrer", referrer)
            .divider()
        )
        if stats.referred_by:
            own_rate = format_affiliate_rate(stats.own_rate_bps)
            own_wagered = math.format_fiat_symbol(stats.own_referred_wagered_usd, "USD")
            own_line = f"{own_rate} of house fee · {own_wagered} wagered since bind"
            nxt = next_affiliate_tier(stats.own_referred_wagered_usd)
            if nxt is not None:
                threshold, bps = nxt
                own_line += (
                    f"\nNext: {format_affiliate_rate(bps)} at "
                    f"{math.format_fiat_symbol(threshold, 'USD')}"
                )
            builder.field("Your commission", own_line).divider()
        tiers = " · ".join(
            f"{format_affiliate_rate(bps)} at {math.format_fiat_symbol(threshold, 'USD')}"
            if threshold > 0
            else f"{format_affiliate_rate(bps)} start"
            for threshold, bps in AFFILIATE_TIERS
        )
        earned = (
            f"{math.format_fiat_symbol(stats.earned_usd, 'USD')} "
            f"({math.format_sol(SolAmount.from_lamports(stats.earned_lamports))} SOL)"
        )
        builder.field("Tiers", tiers).divider()
        builder.field("Referrals", str(stats.referral_count)).divider()
        builder.field("Earned", earned).divider()
        if stats.referrals:
            lines = [
                (
                    f"<@{referral.discord_id}> · "
                    f"{math.format_fiat_symbol(referral.referred_wagered_usd, 'USD')} · "
                    f"{format_affiliate_rate(referral.rate_bps)}"
                )
                for referral in stats.referrals[:10]
            ]
            if stats.referral_count > 10:
                lines.append(f"+{stats.referral_count - 10} more")
            builder.field("Referred players", "\n".join(lines))
        else:
            builder.field(
                "Referred players",
                "None yet. Players lock you in with `/affiliate`.",
            )
        return builder.build()

    def history(
        self,
        user_id: str,
        games: list[GameRecord],
        page: int,
        *,
        total: int,
        page_size: int = 10,
    ) -> CasinoPanel:
        builder = self.builder("Game history")
        pages = max((total + page_size - 1) // page_size, 1)
        builder.footer(f"Page {page + 1} of {pages}")
        if not games:
            return builder.description("No completed games yet.").build()
        start = total - page * page_size
        lines = [
            self._history_line(game, user_id, start - index)
            for index, game in enumerate(games)
        ]
        builder.description("\n".join(lines))
        if page > 0:
            builder.button(
                "Newer",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.history(user_id, page - 1),
            )
        if start - len(games) > 0:
            builder.button(
                "Older",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.history(user_id, page + 1),
            )
        return builder.build()

    def _history_line(self, game: GameRecord, user_id: str, number: int) -> str:
        if game.player_won(user_id):
            mark = "✅"
        elif game.winner_ids or game.winner_id:
            mark = "❌"
        else:
            mark = "➖"
        return (
            f"{number}. {mark} {self._history_kind(game)} "
            f"{self._signed_usd(game.player_profit_fiat(user_id))}"
        )

    def _history_kind(self, game: GameRecord) -> str:
        if game.is_limbo():
            target = (
                f" {format_limbo_multiplier(game.limbo_target)}x"
                if game.limbo_target is not None
                else ""
            )
            return f"Limbo{target} vs House"
        if game.is_mines():
            count = f" {game.mines_count}" if game.mines_count else ""
            return f"Mines{count} vs House"
        if game.is_blackjack():
            return "Blackjack vs House"
        if game.is_jackpot():
            return f"Jackpot {game.matchup_label()}"
        if game.is_target():
            return f"Target {game.matchup_label()}"
        return f"{game.game_type.value.title()} {game.matchup_label()}"

    def settings(self, user: UserRecord, currencies: tuple[str, ...]) -> EmbedBuilder:
        return (
            self.builder(
                "Player settings",
                description=(
                    "Choose how bet amounts are interpreted. "
                    "House games use your client seed plus the next server-seed hash below. "
                    "That hash is locked before you bet; randomizing the client seed does not change it."
                ),
            )
            .field("Currency", user.display_currency)
            .field("Deposit address", f"`{user.deposit_address or 'not ready'}`")
            .field("Client seed", f"`{user.fair_client_seed or 'not ready'}`")
            .field("Next server seed hash", f"`{user.next_server_seed_hash or 'not ready'}`")
            .field("Next nonce", str(user.fair_nonce))
            .timeout(180)
        )

    def invest(
        self,
        member: discord.abc.User,
        position: InvestorPosition,
        quote: PriceQuote,
        currency: str,
    ) -> EmbedBuilder:
        math = self._container.math
        price = quote.sol_price("USD")
        edge = self._container.settings.house_fee_rate_display
        founder = SolAmount.from_lamports(position.founder_equity_lamports)
        pool = SolAmount.from_lamports(position.investor_pool_lamports)
        house_ev = SolAmount.from_lamports(position.forecast_ev_lamports)
        user_ev = SolAmount.from_lamports(position.user_forecast_ev_lamports)
        cash = SolAmount.from_lamports(min(position.user_equity_lamports, position.book_lamports))
        return (
            self.builder(
                "Invest",
                description=(
                    "Your stake is a share of the live house book in SOL. "
                    "House fees and house-game results move the book. "
                    "There is no transfer fee on invest or divest. "
                    f"The {edge} house edge is a forecast only — it is not credited."
                ),
            )
            .thumbnail(member.display_avatar.url)
            .field(
                "Your stake",
                (
                    f"{self._usd_then_sol(position.user_equity_lamports, price)}\n"
                    f"`{position.ownership_bps / 100:.2f}%` of the house book"
                ),
            )
            .field("House book", self._usd_then_sol(position.book_lamports, price))
            .field(
                "Split",
                (
                    f"Founder `{math.format_sol(founder)} SOL`\n"
                    f"Investors `{math.format_sol(pool)} SOL`"
                ),
            )
            .field(
                "Forecast EV",
                (
                    f"Human handle {self._usd_then_sol(position.human_handle_lamports, price)}\n"
                    f"{edge} of that is `{math.format_sol(house_ev)} SOL`\n"
                    f"Your slice `{math.format_sol(user_ev)} SOL`"
                ),
            )
            .field(
                "Range",
                (
                    f"{math.format_fiat_symbol(position.min_invest_usd, 'USD')} – "
                    f"{math.format_fiat_symbol(position.max_invest_usd, 'USD')} per deposit\n"
                    f"Balance `{math.format_sol(SolAmount.from_lamports(position.player_balance_lamports))} SOL` "
                    f"({math.format_fiat_symbol(math.sol_to_fiat(SolAmount.from_lamports(position.player_balance_lamports), quote.sol_price(currency)), currency)})\n"
                    f"Withdrawable `{math.format_sol(cash)} SOL`"
                ),
            )
            .timeout(300)
        )

    def house(self, reserves_lamports: int, fees_lamports: int) -> CasinoPanel:
        math = self._container.math
        return (
            self.builder("House treasury")
            .field("Reserves", f"`{math.format_sol(SolAmount.from_lamports(reserves_lamports))} SOL`")
            .field("Collected fees", f"`{math.format_sol(SolAmount.from_lamports(fees_lamports))} SOL`")
            .build()
        )

    def house_stats(self, report: HouseStatsReport, *, actions: bool = True) -> CasinoPanel:
        price = report.sol_price_usd
        builder = (
            self.builder("House stats")
            .field(
                "House balance",
                self._usd_then_sol(report.house_balance_lamports, price),
            )
            .field(
                "Super wallet",
                self._usd_then_sol(report.super_wallet_lamports, price),
            )
            .field(
                "Founder book",
                self._usd_then_sol(report.founder_pool_lamports, price),
            )
            .field(
                "Investor book",
                self._usd_then_sol(report.investor_pool_lamports, price),
            )
        )
        builder.divider()
        for window in report.timeframes:
            builder.field(window.label, self._timeframe_body(window, price)).divider()
        if actions:
            builder.button(
                "Deposit",
                style=discord.ButtonStyle.success,
                callback=self._on_house_deposit,
            )
            builder.button(
                "Withdraw",
                style=discord.ButtonStyle.secondary,
                callback=self._on_house_withdraw,
            )
        return builder.build()

    async def _on_house_deposit(self, interaction: discord.Interaction) -> None:
        try:
            self._require_house_stats_admin(interaction)
            await interaction.response.send_message(
                **self.house_deposit().send_kwargs(ephemeral=True)
            )
        except Exception as exc:
            await self._send_house_stats_error(interaction, exc)

    async def _on_house_withdraw(self, interaction: discord.Interaction) -> None:
        try:
            self._require_house_stats_admin(interaction)
            treasury = await self._container.admin.house_treasury()
            quote = await self._container.price.get_quote(allow_stale=True)
            house = SolAmount.from_lamports(treasury.sol_balance_lamports)
            balance_text = self._container.math.format_fiat_symbol(
                self._container.math.sol_to_fiat(house, quote.sol_price("USD")),
                "USD",
            )
            from casino.discord_bot.presentation.errors import DiscordErrorPresenter
            from casino.discord_bot.presentation.modals import HouseWithdrawModal

            await interaction.response.send_modal(
                HouseWithdrawModal(
                    self._container,
                    DiscordErrorPresenter(),
                    balance_text=balance_text,
                )
            )
        except Exception as exc:
            await self._send_house_stats_error(interaction, exc)

    async def _send_house_stats_error(self, interaction: discord.Interaction, exc: BaseException) -> None:
        from casino.discord_bot.presentation.errors import DiscordErrorPresenter

        kwargs = DiscordErrorPresenter().panel_for(exc).send_kwargs(ephemeral=True)
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    def _require_house_stats_admin(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if isinstance(member, discord.Member) and member.guild_permissions.administrator:
            return
        self._container.permissions.require_admin(
            member.id,
            [role.id for role in member.roles] if isinstance(member, discord.Member) else [],
        )

    def house_deposit(self) -> CasinoPanel:
        address = self._container.deposits.house_address() or "not ready"
        return (
            self.builder(
                "House deposit",
                description=(
                    "Send **native SOL** to the house wallet. "
                    "The full amount is credited to house reserves and stays on this address — no deposit fee."
                ),
            )
            .field("Deposit Address", f"`{address}`")
            .build()
        )

    def howto(self) -> CasinoPanel:
        fee = self._container.settings.house_fee_rate_display
        min_bet = self._container.math.format_fiat_symbol(
            Decimal(str(self._container.settings.min_bet_usd)),
            "USD",
        )
        return (
            self.builder(
                "How to play",
                description="PvP casino on Solana. Bets use your in-bot SOL balance.",
            )
            .field(
                "Wallet",
                (
                    "Use `/balance` to deposit native SOL to your personal address, then withdraw from there. "
                    "Deposits and withdrawals credit in full. "
                    "`/settings` sets your currency."
                ),
            )
            .divider()
            .field(
                "Games",
                (
                    "**Coinflip** — `/coinflip <amount>` opens a private rules panel. Set **1v1 or 2v2**, Heads/Tails, "
                    "and first-to, then **Create table**. Join from the listing, then every seated player presses **Ready**.\n"
                    "**Dice** — `/dice <amount>` opens a private rules panel. Set matchup and first-to, then **Create table**. "
                    "Highest two-dice total scores the point (in **2v2** that point goes to the team). "
                    "Take turns with `/roll` in the private table.\n"
                    "**Blackjack** — `/blackjack <amount>` deals vs the house in the channel you used. "
                    "8-deck shoe, blackjack pays 3:2, dealer hits soft 17, no peek, no surrender, "
                    "no double after split, split once to two hands, one card on split aces. No extra 3% game fee.\n"
                    "**Limbo** — `/limbo <amount> <multiplier>` vs the house. You win if the crash is at least your target. "
                    "A win pays **target × bet**. Odds include a **3%** house edge (win chance is `97% / target`).\n"
                    "**Mines** — `/mines <amount>` opens a private setup. Pick **1–24** mines, then **Create table**. "
                    "The **5×5** board posts in the channel so others can watch; only you can click. "
                    "Open gems, then **Cash out**. Hit a mine and you lose. "
                    "Odds include a **3%** house edge. No extra 3% game fee.\n"
                    "**Target** — `/target <amount>` opens a table in the channel you used. Set **1v1 / 1v1v1 / 1v1v1v1**, "
                    "then **Create table**. Players **Lock number** (1–999). Closest to the HMAC draw wins. "
                    "If everyone is equally close, it's a push; if a subset ties closest, they split.\n"
                ),
            )
            .divider()
            .field(
                "Fees",
                (
                    f"PvP games take **{fee} of the total pot** when they resolve. "
                    "Blackjack has no extra game fee. Limbo and mines put a 3% edge in the odds. "
                    f"Minimum bet is **{min_bet}**. There is no player vs player max. "
                    "Blackjack, limbo, and mines max bet is **10% of the house book**. "
                    "House-bot exposure on coinflip and dice is also **10% of the house book** per table."
                ),
            )
            .divider()
            .field(
                "Provably fair",
                (
                    "After a game, press **Verify** on the table for seeds and how to replay the result. "
                    "Dice website nonce is `gameNonce:rollIndex`."
                ),
            )
            .divider()
            .field(
                "Affiliate",
                (
                    "`/affiliate @user` locks a referrer. It cannot be changed. "
                    "They earn 5–10% of the house game fee when you win, based on what you wager after the bind. "
                    "House bet winnings are not shared."
                ),
            )
            .divider()
            .field(
                "Other",
                "`/profile` · `/tip` · `/rain` · `/affiliate`. "
                "Rains require a 10% wager and 10% deposit in the last 24 hours, "
                "plus the required custom status.",
            )
            .build()
        )

    def channel_coinflip(self) -> CasinoPanel:
        fee = self._container.settings.house_fee_rate_display
        return self.builder(
            "Coinflip",
            description=(
                "Use `/coinflip <amount> <mode> <side> <first_to>` to open a table. Amount is in your `/settings` currency.\n\n"
                "• Open tables are listed in the **open coinflips** channel.\n"
                "• **1v1** — one player per side. The creator can **Call bot** from the private table.\n"
                "• **2v2** — two players per side; winners split the payout. The creator can call the bot for each empty seat.\n"
                "• Choose **Heads** or **Tails** when you create the table.\n"
                "• **First to** is the number of coin wins needed. Each flip starts 3 seconds after the last.\n"
                "• Every seated player must press **Ready** before the flip starts, or the table auto-flips 1 hour after it fills.\n"
                f"• House fee is **{fee} of the total pot**, taken when the game resolves.\n"
                "• Every flip is provably fair — open the verifier from the provably fair channel."
            ),
        ).build()

    def channel_outcomes(self, game_type: str = "coinflip") -> CasinoPanel:
        return self.builder(
            f"{game_type.title()} outcomes",
            description=f"Resolved **{game_type}** games are posted here: sides, winner, payout, and house fee.",
        ).build()

    def channel_dice(self) -> CasinoPanel:
        fee = self._container.settings.house_fee_rate_display
        return self.builder(
            "Dice",
            description=(
                "Use `/dice <amount> <players> <first_to>` to open a table. Amount is in your `/settings` currency.\n\n"
                "• **1v1, 1v1v1, 1v1v1v1** — free-for-all. Highest two-dice total wins the round.\n"
                "• **2v2** — Team 1 vs Team 2. Highest roll scores a point for that team; teammates split the pot.\n"
                "• Points are scored only after every player in the round has rolled.\n"
                "• In **3+ player** free-for-alls and **2v2** cross-team ties, a high-tie is a roll-off among only those players. "
                "In 1v1, a tie awards **no point**. First to the target score wins the pot.\n"
                "• Take turns with `/roll` in the private table. Rolling out of turn is rejected.\n"
                "• The creator can **Call bot** to fill empty seats.\n"
                f"• House fee is **{fee} of the total pot**, taken when someone wins.\n"
                "• Every roll is provably fair. After the match, press **Verify** and follow the dice steps — "
                "the website nonce is `gameNonce:rollIndex`, not the 0–100 number."
            ),
        ).build()

    def verify_hub(self) -> CasinoPanel:
        return (
            self.builder(
                "Verify",
                description=(
                    "Press **Verify**, enter the 6-digit code from the private follow-up, "
                    "and you'll receive the **Member** role."
                ),
            )
            .persistent()
            .button("Verify", style=discord.ButtonStyle.success, custom_id=self._ids.VERIFY_START)
            .build()
        )

    def verify_challenge(self, code: str) -> CasinoPanel:
        return (
            self.builder(
                "Your verification code",
                description=(
                    f"Enter this code to finish verification. It expires in **5 minutes**.\n\n"
                    f"**`{code}`**"
                ),
            )
            .button("Enter code", style=discord.ButtonStyle.primary, custom_id=self._ids.VERIFY_ENTER)
            .build()
        )

    def channel_open_games(self) -> CasinoPanel:
        return self.builder(
            "Open games",
            description="Open tables are listed here. Join from this channel, then press **Ready** in the table to start.",
        ).build()

    def channel_open_coinflips(self) -> CasinoPanel:
        return self.builder(
            "Open coinflips",
            description="Open **coinflip** tables are listed here. Join from this channel, then press **Ready** in the table to start.",
        ).build()

    def channel_open_dice(self) -> CasinoPanel:
        return self.builder(
            "Open dice",
            description="Open **dice** tables are listed here. Join from this channel, then press **Ready** in the table to start.",
        ).build()

    def channel_open_blackjack(self) -> CasinoPanel:
        return self.builder(
            "Open blackjack",
            description="Blackjack vs the house is dealt here. Use **Hit**, **Stand**, **Split**, and **Double** on the table message.",
        ).build()

    def channel_open_target(self) -> CasinoPanel:
        return self.builder(
            "Open target",
            description=(
                "Open **target** tables are listed here. Join, lock a number from 1 to 999, "
                "or **Call house**. Closest to the draw wins."
            ),
        ).build()

    def channel_deposit_alerts(self) -> CasinoPanel:
        return self.builder(
            "Deposit alerts",
            description="Player and house SOL deposits are posted here when they credit.",
        ).build()

    def channel_withdrawal_alerts(self) -> CasinoPanel:
        return self.builder(
            "Withdrawal alerts",
            description="Completed SOL withdrawals are posted here.",
        ).build()

    def channel_house_ledger(self) -> CasinoPanel:
        return self.builder(
            "House ledger",
            description=(
                "House fees and call-bot win/loss post here after each game. "
                "Amounts are USD, shown as **+$** or **-$**."
            ),
        ).build()

    def deposit_alert(
        self,
        *,
        user_id: str | None,
        amount: SolAmount,
        amount_usd: Decimal,
        signature: str,
        is_house: bool,
    ) -> CasinoPanel:
        who = "House reserves" if is_house or user_id is None else self._mention(user_id)
        return (
            self.builder("Deposit", description=f"{who} deposited **{self._signed_usd(amount_usd)}** (`{self._container.math.format_sol(amount)}`).")
            .build()
        )

    def withdrawal_alert(
        self,
        *,
        user_id: str,
        amount: SolAmount,
        amount_usd: Decimal,
        destination: str,
        signature: str,
    ) -> CasinoPanel:
        return (
            self.builder(
                "Withdrawal",
                description=f"{self._mention(user_id)} withdrew **{self._signed_usd(-amount_usd)}**.",
            )
            .build()
        )

    def house_ledger_alert(self, game: GameRecord) -> CasinoPanel:
        fees = game.house_fee_usd
        games = game.house_game_pnl_fiat()
        net = game.house_net_fiat()
        return (
            self.builder(
                "House ledger",
                description=f"**{self._game_title(game)}** settled.",
            )
            .field("Fees", self._signed_usd(fees))
            .field("Games", self._signed_usd(games))
            .field("Net", self._signed_usd(net))
            .build()
        )

    def guild_config(self, config: GuildConfigRecord) -> EmbedBuilder:
        return (
            self.builder(
                "Casino channel config",
                description=(
                    "Set the open coinflip and dice lobbies, outcome channels, private-table categories, "
                    "the rain channel and optional rain ping, the verify channel and member role, "
                    "jackpot and jackpot history, "
                    "the investor role, support tickets, the provably fair panel, and admin alert channels. "
                    "Blackjack and target open in the channel where the command is used."
                ),
            )
            .field("Open coinflips", self._channel(config.listing_channel_id(GameType.COINFLIP)))
            .field("Open dice", self._channel(config.listing_channel_id(GameType.DICE)))
            .field("Coinflip outcomes", self._channel(config.coinflip_outcomes_channel_id))
            .field("Coinflip category", self._channel(config.coinflip_category_id))
            .field("Dice outcomes", self._channel(config.dice_outcomes_channel_id))
            .field("Dice category", self._channel(config.dice_category_id))
            .field("Blackjack outcomes", self._channel(config.blackjack_outcomes_channel_id))
            .field("Target outcomes", self._channel(config.target_outcomes_channel_id))
            .field("Jackpot", self._channel(config.jackpot_channel_id))
            .field("Jackpot history", self._channel(config.jackpot_history_channel_id))
            .field("Rain", self._channel(config.rain_channel_id))
            .field("Rain ping", self._role(config.rain_ping_role_id))
            .field("Verify", self._channel(config.verify_channel_id))
            .field("Member role", self._role(config.member_role_id))
            .field("Investor role", f"{self._role(config.investor_role_id)} · required for `/invest`")
            .field("Support panel", self._channel(config.support_channel_id))
            .field("Support category", self._channel(config.support_category_id))
            .field("Support role", self._role(config.support_role_id))
            .field("Support logs", self._channel(config.support_logs_channel_id))
            .field("Provably fair", self._channel(config.provably_fair_channel_id))
            .field("Deposit alerts", self._channel(config.deposit_alerts_channel_id))
            .field("Withdrawal alerts", self._channel(config.withdrawal_alerts_channel_id))
            .field("House ledger", self._channel(config.house_ledger_channel_id))
            .timeout(300)
        )

    def support_hub(self) -> CasinoPanel:
        builder = (
            self.builder(
                "Support",
                description=(
                    "Choose a topic, then describe what you need. "
                    "A private ticket channel opens for you and staff. "
                    "One open ticket at a time."
                ),
            )
            .persistent()
        )
        builder.add_item(
            discord.ui.Select(
                custom_id=self._ids.TICKET_TOPIC,
                placeholder="Choose a topic",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(label=category.label(), value=category.value)
                    for category in TicketCategory
                ],
            )
        )
        return builder.build()

    def ticket_room(self, ticket: TicketRecord, *, support_role_id: str | None = None) -> CasinoPanel:
        builder = (
            self.builder(
                f"Ticket #{ticket.number}",
                description=ticket.body,
            )
            .persistent()
            .field("Topic", ticket.category.label())
            .field("Subject", ticket.subject)
            .field("Opened by", f"<@{ticket.opener_id}>")
        )
        if ticket.status is not TicketStatus.CLOSED:
            builder.button(
                "Close",
                style=discord.ButtonStyle.danger,
                custom_id=self._ids.ticket_close(ticket.ticket_id),
            )
        panel = builder.build()
        panel.allowed_mentions = discord.AllowedMentions(users=True, roles=True, everyone=False)
        return panel

    def ticket_closed(self, ticket: TicketRecord, actor_id: str, reason: str) -> CasinoPanel:
        detail = reason.strip() or "No reason given."
        return (
            self.builder(
                f"Ticket #{ticket.number} closed",
                description=detail,
            )
            .field("Topic", ticket.category.label())
            .field("Subject", ticket.subject)
            .field("Opened by", f"<@{ticket.opener_id}>")
            .field("Closed by", f"<@{actor_id}>")
            .build()
        )

    def rain_payout(self, rain: RainRecord, share: SolAmount) -> CasinoPanel:
        math = self._container.math
        pot = math.format_fiat_symbol(rain.amount_fiat, rain.currency)
        return (
            self.builder(
                "Rain ended",
                description=(
                    f"A rain of **{pot}** just settled. "
                    f"Your share `{math.format_sol(share)} SOL` is in your balance."
                ),
            )
            .field("Joiners", f"`{rain.joiner_count()}`")
            .build()
        )

    def rain_listing(self, rain: RainRecord, *, ping_role_id: str | None = None) -> CasinoPanel:
        builder = self.builder("Rain", description=self._rain_description(rain)).persistent()
        if ping_role_id:
            builder.outside(f"<@&{ping_role_id}>")
        if rain.expires_at is not None:
            builder.field(
                "Ends",
                f"{discord.utils.format_dt(rain.expires_at, style='F')} "
                f"({discord.utils.format_dt(rain.expires_at, style='R')})",
            )
        if rain.status is RainStatus.PENDING:
            builder.button(
                "Join",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.rain_join(rain.rain_id),
            )
        panel = builder.build()
        if ping_role_id:
            panel.allowed_mentions = discord.AllowedMentions(
                roles=True, users=False, everyone=False
            )
        return panel

    def rain_join_gate(
        self,
        eligibility: RainJoinEligibility,
        *,
        joined: bool = False,
    ) -> CasinoPanel:
        math = self._container.math
        needed = math.format_fiat_symbol(eligibility.required_fiat, eligibility.currency)
        wagered = math.format_fiat_symbol(eligibility.wagered_fiat, eligibility.currency)
        deposited = math.format_fiat_symbol(eligibility.deposited_fiat, eligibility.currency)
        status = RAIN_JOIN_STATUS.replace("#", "\u2060#")
        if joined:
            title = "Joined the rain"
            extra = "You will be notified with your share when the timer ends."
        elif eligibility.already_joined:
            title = "Already joined"
            extra = "You're already in this rain."
        else:
            title = "Rain requirements"
            extra = "Meet every requirement, then click Join again."
        return (
            self.builder(title, description=extra)
            .field(
                "Wager",
                f"{'✅' if eligibility.wager_ok else '❌'} {wagered} / {needed} in the last 24 hours",
            )
            .field(
                "Deposit",
                f"{'✅' if eligibility.deposit_ok else '❌'} {deposited} / {needed} in the last 24 hours",
            )
            .field(
                "Status",
                f"{'✅' if eligibility.status_ok else '❌'} `{status}`",
            )
            .build()
        )

    def _timeframe_body(self, window: TimeframeStats, sol_price: Decimal) -> str:
        call_pnl = window.house_payout_lamports - window.house_wagered_lamports
        return (
            f"Games: `{window.games_completed}`\n"
            f"Players: `{window.unique_players}`\n"
            f"Volume: {self._usd_then_sol(window.volume_lamports, sol_price)}\n"
            f"Deposits: {self._container.math.format_fiat_symbol(window.deposits_usd, 'USD')}\n"
            f"Withdrawals: {self._container.math.format_fiat_symbol(window.withdrawals_usd, 'USD')}\n"
            f"House games: {self._signed_usd(window.house_games_pnl_usd)}\n"
            f"Fees: {self._usd_then_sol(window.fees_lamports, sol_price)}\n"
            f"Call bot: {self._usd_then_sol(call_pnl, sol_price, signed=True)}\n"
            f"House net: {self._usd_then_sol(window.house_net_lamports, sol_price, signed=True)}"
        )

    def _usd_then_sol(self, lamports: int, sol_price: Decimal, *, signed: bool = False) -> str:
        math = self._container.math
        amount = SolAmount.from_lamports(abs(lamports))
        usd = math.format_fiat_symbol(math.sol_to_fiat(amount, sol_price), "USD")
        sol = f"{math.format_sol(amount)} SOL"
        if not signed:
            return f"{usd} (`{sol}`)"
        sign = "+" if lamports >= 0 else "-"
        return f"{sign}{usd} (`{sign}{sol}`)"

    def _signed_sol(self, lamports: int) -> str:
        sign = "+" if lamports >= 0 else "-"
        return f"{sign}{self._container.math.format_sol(SolAmount.from_lamports(abs(lamports)))} SOL"

    def _signed_usd(self, amount: Decimal) -> str:
        sign = "+" if amount >= 0 else "-"
        return f"{sign}{self._container.math.format_fiat_symbol(abs(amount), 'USD')}"

    def _add_provably_fair_button(self, builder: EmbedBuilder, game: GameRecord) -> None:
        if game.status in {GameStatus.CANCELLED, GameStatus.EXPIRED}:
            return
        builder.button(
            "Verify",
            style=discord.ButtonStyle.secondary,
            custom_id=self._ids.verify_game(game.game_id),
        )

    def _add_listing_actions(self, builder: EmbedBuilder, game: GameRecord) -> None:
        if game.status is not GameStatus.PENDING:
            return
        if game.is_open_ffa() or (not game.is_dice() and game.mode is not GameMode.TEAM):
            builder.button(
                "Join",
                style=discord.ButtonStyle.success,
                custom_id=self._ids.join(game.game_id),
                disabled=game.is_full(),
            )
            return
        if game.is_dice_team():
            builder.button(
                "Join Team 1",
                style=discord.ButtonStyle.primary,
                custom_id=self._ids.join_heads(game.game_id),
                disabled=game.is_full() or not game.vacant_on(CoinSide.HEADS),
            )
            builder.button(
                "Join Team 2",
                style=discord.ButtonStyle.secondary,
                custom_id=self._ids.join_tails(game.game_id),
                disabled=game.is_full() or not game.vacant_on(CoinSide.TAILS),
            )
            return
        builder.button(
            "Join Heads",
            style=discord.ButtonStyle.primary,
            custom_id=self._ids.join_heads(game.game_id),
            disabled=game.is_full() or not game.vacant_on(CoinSide.HEADS),
        )
        builder.button(
            "Join Tails",
            style=discord.ButtonStyle.secondary,
            custom_id=self._ids.join_tails(game.game_id),
            disabled=game.is_full() or not game.vacant_on(CoinSide.TAILS),
        )

    def _add_table_actions(self, builder: EmbedBuilder, game: GameRecord) -> None:
        if game.status is not GameStatus.PENDING:
            return
        builder.button("Ready", style=discord.ButtonStyle.success, custom_id=self._ids.ready(game.game_id))
        if self._can_show_call_bot(game):
            builder.button(
                "Call bot",
                style=discord.ButtonStyle.primary,
                custom_id=self._ids.call_bot(game.game_id),
            )
        builder.button("Leave", style=discord.ButtonStyle.danger, custom_id=self._ids.leave(game.game_id))

    def _players_with_sides(
        self,
        game: GameRecord,
        *,
        ready_status: bool = False,
        result: bool = False,
    ) -> str:
        lines: list[str] = []
        for player_id in game.player_ids():
            if game.is_dice_team():
                side = game.side_of(player_id)
                team = game.team_label(side) if side is not None else "Team"
                line = f"{self._mention(player_id)} ({team}) · {game.score_for(player_id)}"
            elif game.is_dice():
                line = f"{self._mention(player_id)} · {game.score_for(player_id)}"
            else:
                side = "Heads" if player_id in game.heads_ids else "Tails"
                line = f"{self._mention(player_id)} ({side})"
            if result and game.status is GameStatus.COMPLETED:
                line = f"{line} {self._player_pnl(game, player_id)}"
            elif ready_status:
                waiting = "Ready" if is_house_seat(player_id) or game.is_ready(player_id) else "Waiting..."
                line = f"{line} • {waiting}"
            lines.append(line)
        return "\n".join(lines) or "—"

    def _dice_scoreboard(self, game: GameRecord, scores: dict[str, int] | None = None) -> str:
        if game.is_dice_team():
            return self._dice_team_scoreboard(game, scores)
        source = scores if scores is not None else game.scores
        lines = [
            f"{self._mention(player_id)} · {int(source.get(player_id, 0))}"
            for player_id in game.player_ids()
        ]
        return "\n".join(lines) or "—"

    def _mode_label(self, game: GameRecord) -> str:
        if game.is_dice() or (game.first_to or 1) > 1:
            return f"{game.matchup_label()} · First to {game.first_to or 1}"
        return game.matchup_label()

    def _player_pnl(self, game: GameRecord, player_id: str) -> str:
        return self._signed_usd(game.player_profit_fiat(player_id))

    def _rematch_players(self, game: GameRecord, source: GameRecord) -> str:
        lines: list[str] = []
        for player_id in source.human_player_ids():
            status = "Accepted" if game.has_player(player_id) else "Waiting..."
            if source.is_dice_team():
                side = source.side_of(player_id)
                team = source.team_label(side) if side is not None else "Team"
                lines.append(f"{self._mention(player_id)} ({team}) • {status}")
            elif source.is_dice():
                lines.append(f"{self._mention(player_id)} • {status}")
            else:
                side = "Heads" if player_id in source.heads_ids else "Tails"
                lines.append(f"{self._mention(player_id)} ({side}) • {status}")
        return "\n".join(lines) or "—"

    def _game_title(self, game: GameRecord) -> str:
        return game.display_title()

    def player_joined(self, game: GameRecord, joiner_id: str) -> CasinoPanel:
        mode_label = self._mode_label(game)
        builder = self.builder(
            "Player joined",
            description=(
                f"{self._mention(joiner_id)} sat at **{self._game_title(game)}** ({mode_label})."
            ),
        )
        builder.divider()
        builder.field("Wager", self._wager_usd(game))
        return builder.build()

    def _fair_result_label(self, game: GameRecord) -> str:
        if game.is_dice() and game.winner_ids:
            return f"{self._mention(game.winner_ids[0])} won"
        if game.coin_result is not None:
            return game.coin_result.value.title()
        return "—"

    def _fair_mapping_line(self, game: GameRecord, roll: Decimal | None = None) -> str:
        roll_line = f" This game's number is **{roll:.2f}**." if roll is not None else ""
        if game.game_type is GameType.COINFLIP:
            return f"For coinflip, **0–50 = Heads** and **50–100 = Tails**.{roll_line}"
        return (
            "For dice, ignore the website's 0–100 number. Paste nonce as `gameNonce:rollIndex`, "
            "then turn the HMAC hash into two dice (first 8 hex chars = die A, next 8 = die B, each `mod 6 + 1`)."
        )

    def _fair_mapping_field(self, game: GameRecord) -> str:
        if game.game_type is GameType.COINFLIP:
            return "0–50 = Heads · 50–100 = Tails"
        return "Ignore the 0–100 number · two d6 from HMAC hex"

    def _coinflip_score_line(self, game: GameRecord, *, through: int | None = None) -> str:
        if through is None:
            heads = game.side_score(CoinSide.HEADS)
            tails = game.side_score(CoinSide.TAILS)
        else:
            heads = 0
            tails = 0
            for side in game.flip_series()[:through]:
                if side is CoinSide.HEADS:
                    heads += 1
                else:
                    tails += 1
        return f"**Heads {heads}** – **{tails} Tails**"

    def _coinflip_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the game completes"
        target = game.first_to or 1
        description = (
            f"**{self._game_title(game)}** — first to **{target}**.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown before the first flip. Fingerprint of the hidden server seed.\n"
            "• **Server seed** — revealed when the match ends. SHA-256 of this must equal the hash.\n"
            "• **Client seed** — random string for this table. It never changes.\n"
            "• **Game nonce** — this table's starting counter. Flip 1 uses this nonce, "
            "flip 2 uses nonce + 1, and so on.\n\n"
            "**How to use the verifier website**\n"
            "The site has one **Nonce** box and then shows a number from **0 to 100**.\n"
            "1. Paste **Server seed**, **Server seed hash**, and **Client seed**.\n"
            f"2. In **Nonce**, paste `{game.nonce}` for flip 1"
            + (f", `{game.nonce + 1}` for flip 2, and so on." if target > 1 else ".")
            + "\n"
            "3. **0–50 = Heads** and **50–100 = Tails**.\n"
            f"{self._fair_mapping_line(game)}"
        )
        return (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Flips", self._coinflip_fair_flips(game, revealed))
            .button("Open verifier", url=self._container.settings.verify_page_url())
            .build()
        )

    def _coinflip_fair_flips(self, game: GameRecord, revealed: bool) -> str:
        flips = game.flip_series()
        if not flips:
            return f"No flips yet. Flip 1 uses nonce `{game.nonce}`."
        lines = []
        for index, side in enumerate(flips):
            nonce = game.nonce + index
            extra = ""
            if revealed and game.server_seed:
                roll = self._container.fair.resolve_roll(
                    game.server_seed, game.client_seed, game.nonce, index
                )
                extra = f" · verifier **{roll.roll:.2f}**"
            lines.append(f"Flip {index + 1} · nonce `{nonce}` · **{side.value.title()}**{extra}")
        return "\n".join(lines)

    def _dice_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the match ends"
        description = (
            f"**{self._game_title(game)}** — first to **{game.first_to or 1}**.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown before anyone rolls. Fingerprint of the hidden server seed.\n"
            "• **Server seed** — revealed when the match ends. SHA-256 of this must equal the hash.\n"
            "• **Client seed** — random string for this table. It never changes.\n"
            "• **Game nonce** — this table's counter. It never changes.\n"
            "• **Roll index** — which `/roll` in the match, starting at **0**.\n\n"
            "**How to use the verifier website**\n"
            "The site has one **Nonce** box and then shows a 0–100 number. That 0–100 number is for coinflip, not dice.\n"
            f"1. Paste **Server seed**, **Server seed hash**, and **Client seed**.\n"
            f"2. In **Nonce**, paste `gameNonce:rollIndex`. First roll is `{game.nonce}:0`, "
            f"second is `{game.nonce}:1`, and so on.\n"
            "3. Confirm the HMAC hash. Die **A** is the first 8 hex characters, die **B** is the next 8.\n"
            "4. For each group of 8: convert hex to a number, take remainder after dividing by 6, then add 1. "
            "That gives a die from 1 to 6."
        )
        example = self._dice_fair_example(game, revealed)
        if example:
            description = f"{description}\n\n{example}"
        builder = (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Rolls", self._dice_fair_rolls(game))
            .button("Open verifier", url=self._container.settings.verify_page_url())
        )
        return builder.build()

    def _blackjack_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the hand completes"
        description = (
            f"**{self._game_title(game)}** — player vs house.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown when the hand is dealt.\n"
            "• **Server seed** — revealed when the hand ends.\n"
            "• **Client seed** — random string for this table.\n"
            "• **Game nonce** — this table's counter.\n"
            "• **Card index** — which card was drawn, starting at **0**.\n\n"
            "**How to replay a card**\n"
            f"HMAC-SHA256(server_seed, `{game.client_seed}:{game.nonce}:shoe:drawIndex`). First 8 hex characters pick "
            "from the remaining 8-deck shoe (416 cards). Rank is card mod 13 (A,2–10,J,Q,K). Suit is card / 13 (♠♥♦♣)."
        )
        return (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Cards drawn", str(game.blackjack_card_index))
            .button("Open verifier", url=self._container.settings.verify_page_url())
            .build()
        )

    def _limbo_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the round completes"
        target = format_limbo_multiplier(game.limbo_target) if game.limbo_target is not None else "—"
        crash = format_limbo_multiplier(game.limbo_result) if revealed and game.limbo_result is not None else "Hidden"
        description = (
            f"**{self._game_title(game)}** — player vs house.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown when you play.\n"
            "• **Server seed** — revealed when the round ends.\n"
            "• **Client seed** — random string for this round.\n"
            "• **Game nonce** — this round's counter.\n\n"
            "**How to replay the crash**\n"
            f"HMAC-SHA256(server_seed, `{game.client_seed}:{game.nonce}:limbo`). "
            "Take the first 13 hex characters as an integer `n`. "
            "Crash is `floor(((1 − 0.03) / ((n + 1) / 2^52)) × 100) / 100`, "
            "capped between 1.00 and 1,000,000. You win if crash ≥ target."
        )
        return (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Target", f"{target}x")
            .divider()
            .field("Crash", f"{crash}x")
            .button("Open verifier", url=self._container.settings.verify_page_url())
            .build()
        )

    def _mines_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the round completes"
        mines = ", ".join(str(tile + 1) for tile in game.mines_tiles) if revealed and game.mines_tiles else "Hidden"
        description = (
            f"**{self._game_title(game)}** — {game.mines_count} mines on a 5×5 board.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown when the board is dealt.\n"
            "• **Server seed** — revealed when the round ends.\n"
            "• **Client seed** — random string for this round.\n"
            "• **Game nonce** — this round's counter.\n\n"
            "**How to replay the mines**\n"
            f"HMAC-SHA256(server_seed, `{game.client_seed}:{game.nonce}:mines:drawIndex`). "
            "For each mine, the first 8 hex characters pick an unused tile from the remaining 25. "
            "Tiles are numbered 1–25, left to right, top to bottom."
        )
        return (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Mines", mines)
            .button("Open verifier", url=self._container.settings.verify_page_url())
            .build()
        )

    def _target_fair(self, game: GameRecord, revealed: bool) -> CasinoPanel:
        seed = f"`{game.server_seed}`" if revealed and game.server_seed else "Hidden until the draw"
        description = (
            f"**{self._game_title(game)}** — closest to 1–999.\n\n"
            "**What each number means**\n"
            "• **Server seed hash** — shown when the table is listed.\n"
            "• **Server seed** — revealed when the draw completes.\n"
            "• **Client seed** — random string for this table.\n"
            "• **Game nonce** — this table's counter.\n\n"
            "**How to replay the draw**\n"
            f"HMAC-SHA256(server_seed, `{game.client_seed}:{game.nonce}:target:0`). "
            "First 8 hex characters, convert to an integer, remainder after dividing by 999, then add 1. "
            "House picks use index **1** instead of **0**."
        )
        draw = str(game.target_number) if revealed and game.target_number is not None else "Hidden"
        return (
            self.builder("Provably fair check", description=description)
            .divider()
            .field("Server seed hash", f"`{game.server_seed_hash}`")
            .divider()
            .field("Server seed", seed)
            .divider()
            .field("Client seed", f"`{game.client_seed}`")
            .divider()
            .field("Game nonce", str(game.nonce))
            .divider()
            .field("Draw", draw)
            .button("Open verifier", url=self._container.settings.verify_page_url())
            .build()
        )

    def _target_player_lines(self, game: GameRecord, *, reveal: bool = True) -> str:
        lines: list[str] = []
        for player_id in game.player_ids():
            name = self._mention(player_id)
            if game.status is GameStatus.COMPLETED and reveal:
                pick = game.target_picks.get(player_id)
                shown = pick if pick is not None else "—"
                lines.append(f"{name} · **{shown}** · {self._player_balance_change(game, player_id)}")
            elif game.has_target_pick(player_id) or game.status is GameStatus.COMPLETED:
                lines.append(f"{name} · Locked")
            else:
                lines.append(f"{name} · Waiting...")
        if game.status is GameStatus.PENDING:
            open_seats = game.seat_capacity() - len(game.player_ids())
            for _ in range(max(open_seats, 0)):
                lines.append("Open seat")
        return "\n".join(lines) or "—"

    def _target_result_line(self, game: GameRecord) -> str:
        if not game.winner_ids:
            return "Push. Wagers were returned."
        if all(is_house_seat(player_id) for player_id in game.winner_ids):
            return "House was closest."
        return f"{self._mention(game.winner_ids[0])} was closest."

    def _blackjack_dealer_line(self, game: GameRecord, *, cards: list | None = None) -> str:
        shown = game.blackjack_dealer_cards if cards is None else cards
        if not shown:
            return "—"
        hole_hidden = cards is None and game.blackjack_hole_hidden and len(shown) >= 2
        if hole_hidden:
            visible = " ".join(card.label for card in shown[:-1])
            return f"{visible} ??  ·  {shown[0].pip_value if shown[0].rank != 0 else 11}+"
        labels = " ".join(card.label for card in shown)
        return f"{labels}  ·  **{hand_total(shown)}**"

    def _blackjack_player_lines(self, game: GameRecord, *, show_outcome: bool = True) -> str:
        if not game.blackjack_player_hands:
            return "—"
        lines: list[str] = []
        for index, hand in enumerate(game.blackjack_player_hands):
            labels = " ".join(card.label for card in hand.cards) or "—"
            marker = " ←" if game.status is GameStatus.ACTIVE and index == game.blackjack_hand_index else ""
            change = (
                f" · {self._blackjack_hand_change(game, hand)}"
                if show_outcome and hand.outcome
                else ""
            )
            prefix = f"Hand {index + 1} · " if len(game.blackjack_player_hands) > 1 else ""
            lines.append(f"{prefix}{labels}  ·  **{hand_total(hand.cards)}**{change}{marker}")
        return "\n".join(lines)

    def _lamports_usd(self, lamports: int) -> str:
        sol = SolAmount.from_lamports(max(int(lamports), 0))
        quote = self._container.price.peek_cache()
        if quote is None:
            return f"{self._container.math.format_sol(sol)} SOL"
        usd = self._container.math.sol_to_fiat(sol, quote.sol_price("USD"))
        return self._container.math.format_fiat_symbol(usd, "USD")

    async def user_balance_line(self, game: GameRecord, *, conceal_payout: bool = False) -> str:
        return await self._blackjack_balance_line(game, conceal_payout=conceal_payout)

    async def _blackjack_balance_line(self, game: GameRecord, *, conceal_payout: bool = False) -> str:
        try:
            user = await self._container.users.get_or_create(game.creator_id)
        except Exception:
            return ""
        lamports = user.sol_balance.lamports
        if conceal_payout:
            lamports -= int(game.winner_payout_lamports or 0)
        sol = SolAmount.from_lamports(max(lamports, 0))
        quote = self._container.price.peek_cache()
        if quote is None:
            return f"Balance **{self._container.math.format_sol(sol)} SOL**"
        usd = self._container.math.sol_to_fiat(sol, quote.sol_price("USD"))
        return f"Balance **{self._container.math.format_fiat_symbol(usd, 'USD')}**"

    def _player_balance_change(self, game: GameRecord, player_id: str) -> str:
        quote = self._container.price.peek_cache()
        lamports = game.player_profit_lamports(player_id)
        if quote is None:
            sign = "+" if lamports >= 0 else "-"
            return f"{sign}{self._container.math.format_sol(SolAmount.from_lamports(abs(lamports)))} SOL"
        usd = self._container.math.sol_to_fiat(SolAmount.from_lamports(abs(lamports)), quote.sol_price("USD"))
        return self._signed_usd(usd if lamports >= 0 else -usd)

    def _blackjack_hand_change(self, game: GameRecord, hand) -> str:
        if len(game.blackjack_player_hands) == 1:
            return self._player_balance_change(game, game.creator_id)
        quote = self._container.price.peek_cache()
        lamports = self._blackjack_hand_net_lamports(hand)
        if quote is None:
            sign = "+" if lamports >= 0 else "-"
            return f"{sign}{self._container.math.format_sol(SolAmount.from_lamports(abs(lamports)))} SOL"
        usd = self._container.math.sol_to_fiat(SolAmount.from_lamports(abs(lamports)), quote.sol_price("USD"))
        return self._signed_usd(usd if lamports >= 0 else -usd)

    def _blackjack_hand_net_lamports(self, hand) -> int:
        wager = int(hand.bet_lamports)
        if hand.outcome is BlackjackOutcome.PUSH:
            return 0
        if hand.outcome is BlackjackOutcome.LOSE:
            return -wager
        if hand.outcome is BlackjackOutcome.BLACKJACK:
            return wager + wager // 2
        return wager

    def _vs_house_title(self, game: GameRecord, name: str) -> str:
        if not game.winner_ids:
            return name
        if game.winner_ids == [game.creator_id]:
            return f"✅ {name}"
        return f"❌ {name}"

    def _blackjack_result_line(self, game: GameRecord) -> str:
        if not game.winner_ids:
            return "Push. Wagers were returned."
        if game.winner_ids == [game.creator_id]:
            if any(hand.outcome is BlackjackOutcome.BLACKJACK for hand in game.blackjack_player_hands):
                return f"{self._mention(game.creator_id)} blackjack pays 3:2."
            return f"{self._mention(game.creator_id)} beat the house."
        return "House wins."

    def _dice_fair_rolls(self, game: GameRecord) -> str:
        if not game.dice_rolls:
            return f"No rolls yet. After the first `/roll`, check nonce `{game.nonce}:0`."
        lines = []
        for roll in game.dice_rolls:
            lines.append(
                f"`{roll.roll_index}` {self._mention(roll.player_id)} · "
                f"**{roll.die_a}** + **{roll.die_b}** = **{roll.total}** · "
                f"website nonce `{game.nonce}:{roll.roll_index}`"
            )
        return "\n".join(lines)

    def _dice_fair_example(self, game: GameRecord, revealed: bool) -> str:
        if not revealed or not game.server_seed or not game.dice_rolls:
            return ""
        roll = game.dice_rolls[0]
        fair = self._container.fair.resolve_dice(
            game.server_seed, game.client_seed, game.nonce, roll.roll_index
        )
        hex_a = fair.digest[0:8]
        hex_b = fair.digest[8:16]
        raw_a = int(hex_a, 16)
        raw_b = int(hex_b, 16)
        return (
            f"**Worked example — roll {roll.roll_index}**\n"
            f"Website nonce `{game.nonce}:{roll.roll_index}`.\n"
            f"HMAC `{fair.digest}`\n"
            f"Die A `{hex_a}` → {raw_a} % 6 + 1 = **{fair.die_a}**\n"
            f"Die B `{hex_b}` → {raw_b} % 6 + 1 = **{fair.die_b}**\n"
            f"Total **{fair.total}**, matching **{roll.die_a}** + **{roll.die_b}** on the table."
        )

    def _can_show_call_bot(self, game: GameRecord) -> bool:
        return game.can_call_house_seat(max_house=1, seat=0)

    def _wager_usd(self, game: GameRecord) -> str:
        math = self._container.math
        native = math.format_fiat_symbol(game.bet_amount_fiat, game.currency)
        if game.currency.upper() == "USD":
            return native
        return f"{native} ({math.format_fiat_symbol(game.stake_usd(), 'USD')})"

    def _winner_line(self, game: GameRecord) -> str:
        mentions = [self._mention(player_id) for player_id in game.winner_ids]
        if not mentions:
            return "Nobody won."
        pot = self._container.math.format_fiat_symbol(self._pot_after_fee_fiat(game), "USD")
        if len(mentions) == 1:
            return f"{mentions[0]} has won the match."
        return f"{' & '.join(mentions)} have won the match."

    def _pot_after_fee_fiat(self, game: GameRecord) -> Decimal:
        cents = int((game.bet_amount_fiat / _FIAT_CENTS).to_integral_value(rounding=ROUND_DOWN))
        total_cents = cents * max(len(game.player_ids()), 1)
        fee_cents = total_cents * self._container.settings.house_fee_bps // 10_000
        return Decimal(total_cents - fee_cents) / Decimal(100)

    def _add_dice_teams(
        self,
        builder: EmbedBuilder,
        game: GameRecord,
        *,
        ready_status: bool = False,
        result: bool = False,
        pad_open: bool = True,
        scores: dict[str, int] | None = None,
    ) -> EmbedBuilder:
        source = scores if scores is not None else game.scores
        show_score = game.status is not GameStatus.PENDING or scores is not None
        for side in (CoinSide.HEADS, CoinSide.TAILS):
            members = game.ids_for(side)
            score = int(source.get(members[0], 0)) if members else 0
            title = f"{game.team_label(side)} · {score}" if show_score else game.team_label(side)
            builder.field(
                title,
                self._side_list(
                    game,
                    side,
                    ready_status=ready_status,
                    result=result,
                    pad_open=pad_open,
                ),
            )
        return builder

    def _dice_team_scoreboard(self, game: GameRecord, scores: dict[str, int] | None = None) -> str:
        source = scores if scores is not None else game.scores
        lines: list[str] = []
        for side in (CoinSide.HEADS, CoinSide.TAILS):
            members = game.ids_for(side)
            score = int(source.get(members[0], 0)) if members else 0
            names = ", ".join(self._mention(player_id) for player_id in members) or "—"
            lines.append(f"**{game.team_label(side)} · {score}** — {names}")
        return "\n".join(lines) or "—"

    def _add_coinflip_sides(
        self,
        builder: EmbedBuilder,
        game: GameRecord,
        *,
        ready_status: bool = False,
        result: bool = False,
        pad_open: bool = True,
        conceal_score: bool = False,
    ) -> EmbedBuilder:
        show_score = not conceal_score and (result or game.status is not GameStatus.PENDING)
        for side in (CoinSide.HEADS, CoinSide.TAILS):
            title = side.value.title()
            if show_score:
                title = f"{title} · {game.side_score(side)}"
            builder.field(
                title,
                self._side_list(
                    game,
                    side,
                    ready_status=ready_status,
                    result=result,
                    pad_open=pad_open,
                ),
            )
        return builder

    def _side_list(
        self,
        game: GameRecord,
        side: CoinSide,
        *,
        ready_status: bool = False,
        result: bool = False,
        pad_open: bool = True,
    ) -> str:
        lines: list[str] = []
        for player_id in game.ids_for(side):
            line = self._mention(player_id)
            if result and game.status is GameStatus.COMPLETED:
                line = f"{line} {self._player_pnl(game, player_id)}"
            elif ready_status:
                waiting = "Ready" if is_house_seat(player_id) or game.is_ready(player_id) else "Waiting..."
                line = f"{line} • {waiting}"
            lines.append(line)
        if pad_open:
            open_seats = game.seats_per_side() - len(lines)
            lines.extend(["—"] * max(open_seats, 0))
        return "\n".join(lines) or "—"

    def _rain_description(self, rain: RainRecord) -> str:
        math = self._container.math
        pot = math.format_fiat_symbol(rain.amount_fiat, rain.currency)
        who = self._mention(rain.creator_id)
        joiners = rain.joiner_count()
        if rain.status is RainStatus.COMPLETED:
            share = self._rain_share_fiat(rain)
            share_text = math.format_fiat_symbol(share, rain.currency) if share is not None else pot
            if joiners == 1:
                return f"{who} started a rain of **{pot}**. **1** joined and received **{share_text}**."
            return (
                f"{who} started a rain of **{pot}**. **{joiners}** joined "
                f"and each received **{share_text}**."
            )
        if rain.status is RainStatus.REFUNDED:
            return f"{who} started a rain of **{pot}**. **0** joined, so the pot was returned."
        if rain.status is RainStatus.SETTLING:
            return f"{who} started a rain of **{pot}**. **{joiners}** joined. Settling."
        return (
            f"{who} started a rain of **{pot}**. **{joiners}** joined so far. "
            "Click Join for an equal share when the timer ends."
        )

    def _rain_share_fiat(self, rain: RainRecord) -> Decimal | None:
        if not rain.joiner_ids or not rain.amount_lamports:
            return None
        if rain.payouts_lamports:
            lamports = rain.payouts_lamports.get(rain.joiner_ids[0], 0)
            return rain.amount_fiat * Decimal(lamports) / Decimal(rain.amount_lamports)
        return rain.amount_fiat / Decimal(len(rain.joiner_ids))

    def _mention(self, player_id: str) -> str:
        if is_house_seat(player_id):
            return "**House**"
        return f"<@{player_id}>"

    def _channel(self, channel_id: str | None) -> str:
        return f"<#{channel_id}>" if channel_id else "Not set"

    def _role(self, role_id: str | None) -> str:
        return f"<@&{role_id}>" if role_id else "Not set"
