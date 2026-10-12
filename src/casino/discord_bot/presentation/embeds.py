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
from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType, RainStatus
from casino.domain.records import (
    DiceRollRecord,
    DiceTurnEvent,
    GameRecord,
    GuildConfigRecord,
    HouseStatsReport,
    PlayerProfileStats,
    RainRecord,
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
                    "Send **native SOL** to your personal deposit address. It stays the same for you. A **1%** deposit fee is credited to the house book."
                ),
            )
            .field("Deposit Address", f"`{address}`")
            .build()
        )

    def open_game(self, game: GameRecord) -> CasinoPanel:
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
                "Every game commits a random server seed by publishing `SHA256(server_seed)` "
                "before play starts. A random client seed and game nonce are shown on the "
                "table and never change. The server seed stays hidden until the game completes.\n\n"
                "Press **Verify** on a result, then paste the values into the verifier.\n"
                "• **Coinflip** — paste the nonce as-is. The site's 0–100 number is the result "
                "(0–50 Heads, 50–100 Tails).\n"
                "• **Dice** — paste nonce as `gameNonce:rollIndex` (first roll is `N:0`). "
                "Ignore the site's 0–100 number; the HMAC hash becomes the two dice."
            ),
        )
        return builder.persistent().build()

    def provably_fair(self, game: GameRecord) -> CasinoPanel:
        revealed = game.status is GameStatus.COMPLETED and bool(game.server_seed)
        if game.is_dice():
            return self._dice_fair(game, revealed)
        return self._coinflip_fair(game, revealed)

    def joined_table(self, game: GameRecord) -> CasinoPanel:
        title = self._game_title(game)
        mode = self._mode_label(game)
        if game.private_channel_id:
            description = (
                f"You joined **{title}** ({mode}).\n"
                f"Open the table: <#{game.private_channel_id}>"
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
        mark = "✅" if game.player_won(user_id) else "❌"
        kind = "Dice" if game.is_dice() else "Coinflip"
        return (
            f"{number}. {mark} {kind} {game.matchup_label()} "
            f"{self._signed_usd(game.player_profit_fiat(user_id))}"
        )

    def settings(self, user: UserRecord, currencies: tuple[str, ...]) -> EmbedBuilder:
        return (
            self.builder(
                "Player settings",
                description="Choose how bet amounts are interpreted.",
            )
            .field("Currency", user.display_currency)
            .field("Deposit address", f"`{user.deposit_address or 'not ready'}`")
            .timeout(180)
        )

    def house(self, reserves_lamports: int, fees_lamports: int) -> CasinoPanel:
        math = self._container.math
        return (
            self.builder("House treasury")
            .field("Reserves", f"`{math.format_sol(SolAmount.from_lamports(reserves_lamports))} SOL`")
            .field("Collected fees", f"`{math.format_sol(SolAmount.from_lamports(fees_lamports))} SOL`")
            .build()
        )

    def house_stats(self, report: HouseStatsReport) -> CasinoPanel:
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
        )
        builder.divider()
        for window in report.timeframes:
            builder.field(window.label, self._timeframe_body(window, price)).divider()
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
        self._container.permissions.require_admin(
            interaction.user.id,
            [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else [],
        )

    def house_deposit(self) -> CasinoPanel:
        address = self._container.deposits.house_address() or "not ready"
        return (
            self.builder(
                "House deposit",
                description=(
                    "Send **native SOL** to the house deposit address. "
                    "The full amount is credited to house reserves — no deposit fee."
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
                    "Player deposits and withdrawals take a **1%** transfer fee credited to the house book. "
                    "`/settings` sets your currency."
                ),
            )
            .divider()
            .field(
                "Coinflip",
                (
                    "`/coinflip <amount> <1v1|2v2> <Heads|Tails> <first_to>` opens a table. "
                    "Join from the listing, then every seated player presses **Ready**. "
                    "**First to** is coin wins needed; each flip starts 3 seconds after the last. "
                    "The creator can **Call bot** to fill empty seats. "
                    "In the home server it posts to open coinflips; as a user app it posts inline."
                ),
            )
            .divider()
            .field(
                "Dice",
                (
                    "`/dice <amount> <1v1|1v1v1|1v1v1v1|2v2> <first_to>` opens a table. "
                    "Highest two-dice total scores the point. In **2v2**, that point goes to the team. "
                    "Take turns with `/roll` in the private table. Ties in 1v1 score nothing; "
                    "3+ player and cross-team ties roll off."
                ),
            )
            .divider()
            .field(
                "Fees",
                (
                    f"House fee is **{fee} of the total pot**, taken when the game resolves. "
                    f"Minimum bet is **{min_bet}**. There is no player vs player max. "
                    "House-bot exposure is capped at $1,000 USD per table."
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
                "`/profile` · `/tip` · `/rain` · `/affiliate`",
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
            .field("Destination", f"`{destination}`")
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
                    "the provably fair panel, and admin alert channels."
                ),
            )
            .field("Open coinflips", self._channel(config.listing_channel_id(GameType.COINFLIP)))
            .field("Open dice", self._channel(config.listing_channel_id(GameType.DICE)))
            .field("Coinflip outcomes", self._channel(config.coinflip_outcomes_channel_id))
            .field("Coinflip category", self._channel(config.coinflip_category_id))
            .field("Dice outcomes", self._channel(config.dice_outcomes_channel_id))
            .field("Dice category", self._channel(config.dice_category_id))
            .field("Rain", self._channel(config.rain_channel_id))
            .field("Rain ping", self._role(config.rain_ping_role_id))
            .field("Verify", self._channel(config.verify_channel_id))
            .field("Member role", self._role(config.member_role_id))
            .field("Provably fair", self._channel(config.provably_fair_channel_id))
            .field("Deposit alerts", self._channel(config.deposit_alerts_channel_id))
            .field("Withdrawal alerts", self._channel(config.withdrawal_alerts_channel_id))
            .field("House ledger", self._channel(config.house_ledger_channel_id))
            .timeout(300)
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

    def _timeframe_body(self, window: TimeframeStats, sol_price: Decimal) -> str:
        call_pnl = window.house_payout_lamports - window.house_wagered_lamports
        return (
            f"Games: `{window.games_completed}`\n"
            f"Players: `{window.unique_players}`\n"
            f"Volume: {self._usd_then_sol(window.volume_lamports, sol_price)}\n"
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
        if game.is_dice_ffa() or (not game.is_dice() and game.mode is not GameMode.TEAM):
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
        cap = Decimal(self._container.settings.max_call_bot_usd)
        return game.can_call_house_seat(max_house_usd=cap, seat_usd=self._seat_usd(game))

    def _seat_usd(self, game: GameRecord) -> Decimal:
        if game.bet_amount_usd > 0:
            return game.bet_amount_usd
        if game.currency.upper() == "USD":
            return game.bet_amount_fiat
        quote = self._container.price.peek_cache()
        if quote is None:
            return game.bet_amount_fiat
        return self._container.math.sol_to_fiat(game.bet_amount, quote.sol_price("USD"))

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
