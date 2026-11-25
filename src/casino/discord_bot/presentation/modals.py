from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.utils.money import SolAmount


class WithdrawModal(discord.ui.Modal):
    def __init__(
        self,
        container: AppContainer,
        errors: DiscordErrorPresenter,
        *,
        currency: str,
        balance_text: str,
    ) -> None:
        super().__init__(title=f"Withdraw · {balance_text}"[:45])
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = errors
        self._currency = currency
        self.amount = discord.ui.TextInput(
            label=f"Amount in {currency}",
            placeholder=f"You have {balance_text}",
        )
        self.destination = discord.ui.TextInput(
            label="Destination wallet",
            required=True,
            min_length=32,
            max_length=64,
            placeholder="Solana address",
        )
        self.add_item(self.amount)
        self.add_item(self.destination)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            quote = await self._container.price.get_quote(allow_stale=True)
            sol_amount = self._container.math.fiat_to_sol(fiat, quote.sol_price(self._currency))
            dest = str(self.destination.value).strip()
            result = await self._container.withdrawals.request(str(interaction.user.id), sol_amount, dest)
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        await interaction.followup.send(
            **self._embeds.success(
                "Withdrawal sent",
                (
                    f"Sent `{self._container.math.format_sol(result.amount)} SOL` to `{result.destination}`.\n"
                    f"Signature `{result.signature}`."
                ),
            ).send_kwargs(ephemeral=True)
        )


class HouseWithdrawModal(discord.ui.Modal):
    def __init__(
        self,
        container: AppContainer,
        errors: DiscordErrorPresenter,
        *,
        balance_text: str,
    ) -> None:
        super().__init__(title=f"Founder withdraw · {balance_text}"[:45])
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = errors
        self.amount = discord.ui.TextInput(
            label="Amount in USD",
            placeholder=f"Founder share {balance_text}",
        )
        self.destination = discord.ui.TextInput(
            label="Destination wallet",
            required=True,
            min_length=32,
            max_length=64,
            placeholder="Solana address",
        )
        self.add_item(self.amount)
        self.add_item(self.destination)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            self._container.permissions.require_admin(
                interaction.user.id,
                [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else [],
            )
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            quote = await self._container.price.get_quote(allow_stale=True)
            sol_amount = self._container.math.fiat_to_sol(fiat, quote.sol_price("USD"))
            dest = str(self.destination.value).strip()
            result = await self._container.admin.withdraw_house(
                sol_amount,
                dest,
                str(interaction.user.id),
            )
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        await interaction.followup.send(
            **self._embeds.success(
                "House withdrawal sent",
                (
                    f"Sent `{self._container.math.format_sol(result.amount)} SOL` to `{result.destination}`.\n"
                    f"Signature `{result.signature}`."
                ),
            ).send_kwargs(ephemeral=True)
        )


class InvestAmountModal(discord.ui.Modal):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        *,
        currency: str,
        available_text: str,
        withdraw: bool,
    ) -> None:
        super().__init__(title=("Withdraw stake" if withdraw else "Invest in house")[:45])
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._currency = currency
        self._withdraw = withdraw
        self.amount = discord.ui.TextInput(
            label=f"Amount in {currency}",
            placeholder=available_text,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.presentation.invest_view import require_investor_role

        await interaction.response.defer(ephemeral=True)
        try:
            await require_investor_role(interaction, self._container)
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            user = await self._container.users.get_or_create(str(interaction.user.id))
            quote = await self._container.price.get_quote(allow_stale=True)
            sol_amount = self._container.math.fiat_to_sol(fiat, quote.sol_price(user.display_currency))
            if self._withdraw:
                position = await self._container.investors.withdraw(str(interaction.user.id), sol_amount)
                title = "Stake withdrawn"
                verb = "Returned"
            else:
                position = await self._container.investors.deposit(str(interaction.user.id), sol_amount)
                title = "Invested"
                verb = "Moved"
            stake = self._container.math.format_sol(SolAmount.from_lamports(position.user_equity_lamports))
            await interaction.followup.send(
                **self._embeds.success(
                    title,
                    (
                        f"{verb} `{self._container.math.format_sol(sol_amount)} SOL` "
                        f"with no transfer fee.\n"
                        f"Your house stake is now `{stake} SOL` "
                        f"({position.ownership_bps / 100:.2f}% of the book)."
                    ),
                ).send_kwargs(ephemeral=True)
            )
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))


class WalletModal(discord.ui.Modal, title="Withdrawal wallet"):
    wallet = discord.ui.TextInput(label="Solana address", min_length=32, max_length=64)

    def __init__(self, container: AppContainer, embeds: EmbedFactory, errors: DiscordErrorPresenter) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self._container.settings_service.update_wallet(
                str(interaction.user.id), str(self.wallet.value)
            )
            await interaction.followup.send(
                **self._embeds.success("Wallet saved", f"`{user.wallet_address}`").send_kwargs(ephemeral=True)
            )
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))


class ProvablyFairCheckModal(discord.ui.Modal, title="Check a game"):
    game_id = discord.ui.TextInput(
        label="Game ID",
        placeholder="Paste the game ID from history or a result",
        min_length=4,
        max_length=64,
    )

    def __init__(self, container: AppContainer, embeds: EmbedFactory, errors: DiscordErrorPresenter) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            game = await self._container.games.get(str(self.game_id.value).strip())
            await interaction.followup.send(**self._embeds.provably_fair(game).send_kwargs(ephemeral=True))
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))


class VerifyCodeModal(discord.ui.Modal, title="Enter verification code"):
    code = discord.ui.TextInput(
        label="6-digit code",
        placeholder="123456",
        min_length=6,
        max_length=6,
    )

    def __init__(self, container: AppContainer, embeds: EmbedFactory, errors: DiscordErrorPresenter) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                **self._embeds.error("Verify in the server.").send_kwargs(ephemeral=True)
            )
            return
        guild_id = str(interaction.guild.id)
        if not self._container.verification.check(guild_id, str(interaction.user.id), str(self.code.value)):
            await interaction.response.send_message(
                **self._embeds.error(
                    "That code is wrong or expired. Press **Verify** again for a new code."
                ).send_kwargs(ephemeral=True)
            )
            return
        config = await self._container.guild_configs.get(guild_id)
        member = interaction.user
        member_role = (
            interaction.guild.get_role(int(config.member_role_id)) if config.member_role_id else None
        )
        if member_role is None:
            await interaction.response.send_message(
                **self._embeds.error("Verification is not configured. Ask an admin.").send_kwargs(
                    ephemeral=True
                )
            )
            return
        if member_role not in member.roles:
            try:
                await member.add_roles(member_role, reason="Passed verification")
            except discord.HTTPException:
                await interaction.response.send_message(
                    **self._embeds.error(
                        "The code was correct, but I could not add the Member role. Ask an admin."
                    ).send_kwargs(ephemeral=True)
                )
                return
        await interaction.response.send_message(
            **self._embeds.success(
                "Verified",
                "You're verified. The Member role has been added.",
            ).send_kwargs(ephemeral=True)
        )


class TargetPickModal(discord.ui.Modal):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__(title="Lock your number")
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id
        self.number = discord.ui.TextInput(
            label="Number (1-999)",
            placeholder="250",
            min_length=1,
            max_length=3,
        )
        self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from casino.discord_bot.bot import CasinoBot
            from casino.domain.enums import GameStatus

            game = await self._container.games.lock_target_pick(
                self._game_id, str(interaction.user.id), self.number.value
            )
            bot = interaction.client
            if isinstance(bot, CasinoBot):
                if game.status is GameStatus.COMPLETED:
                    await bot.tables.on_resolved(game)
                else:
                    await bot.tables.on_lobby_changed(game)
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        if game.status is GameStatus.COMPLETED:
            detail = "The draw is in. Check the table for the result."
        else:
            detail = "Your number is locked. The table stays up until everyone has locked."
        await interaction.followup.send(
            **self._embeds.success("Number locked", detail).send_kwargs(ephemeral=True)
        )


class TicketCreateModal(discord.ui.Modal, title="Open a ticket"):
    subject = discord.ui.TextInput(label="Subject", max_length=80, placeholder="Short summary")
    details = discord.ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="What happened, and what should staff look at?",
    )

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        category,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._category = category

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from casino.discord_bot.bot import CasinoBot

            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            ticket = await bot.tickets.open_ticket(
                interaction,
                self._category,
                str(self.subject.value),
                str(self.details.value),
            )
            mention = f"<#{ticket.channel_id}>" if ticket.channel_id else "a private channel"
            await interaction.followup.send(
                **self._embeds.success(
                    f"Ticket #{ticket.number}",
                    f"Staff will reply in {mention}.",
                ).send_kwargs(ephemeral=True)
            )
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))


class TicketCloseModal(discord.ui.Modal, title="Close ticket"):
    reason = discord.ui.TextInput(
        label="Reason (optional)",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph,
        placeholder="Resolved, duplicate, no response...",
    )

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        ticket_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._ticket_id = ticket_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot

        await interaction.response.defer(ephemeral=True)
        try:
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tickets.handle_close(interaction, self._ticket_id, str(self.reason.value or ""))
            await interaction.followup.send(
                **self._embeds.success("Ticket closed", "A transcript was saved if logs are configured.").send_kwargs(
                    ephemeral=True
                )
            )
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))


class LimboBetModal(discord.ui.Modal, title="Change bet"):
    amount = discord.ui.TextInput(label="New bet amount", placeholder="5", max_length=16)
    multiplier = discord.ui.TextInput(label="Target multiplier", placeholder="2.00", max_length=16)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot
        from casino.domain.limbo import parse_limbo_multiplier

        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            target = parse_limbo_multiplier(str(self.multiplier.value))
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tables.handle_limbo_redeal(
                self._game_id,
                str(interaction.user.id),
                fiat_amount=fiat,
                limbo_target=target,
                interaction=interaction,
            )
        except Exception as exc:
            panel = self._errors.panel_for(exc)
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class MinesBetModal(discord.ui.Modal, title="Change bet"):
    amount = discord.ui.TextInput(label="New bet amount", placeholder="5", max_length=16)
    mines = discord.ui.TextInput(label="Mines (1-24)", placeholder="3", max_length=2)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot
        from casino.domain.mines import parse_mines_count

        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            mines = parse_mines_count(str(self.mines.value))
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tables.handle_mines_redeal(
                self._game_id,
                str(interaction.user.id),
                fiat_amount=fiat,
                mines_count=mines,
                interaction=interaction,
            )
        except Exception as exc:
            panel = self._errors.panel_for(exc)
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class HouseBaccaratBetModal(discord.ui.Modal, title="Change bet"):
    amount = discord.ui.TextInput(label="New bet amount", placeholder="5", max_length=16)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot

        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tables.handle_house_baccarat_redeal(
                self._game_id,
                str(interaction.user.id),
                fiat_amount=fiat,
                interaction=interaction,
            )
        except Exception as exc:
            panel = self._errors.panel_for(exc)
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class TowerBetModal(discord.ui.Modal, title="Change bet"):
    amount = discord.ui.TextInput(label="New bet amount", placeholder="5", max_length=16)
    difficulty = discord.ui.TextInput(label="Difficulty (easy, medium, hard)", placeholder="easy", max_length=8)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot
        from casino.domain.tower import parse_tower_difficulty

        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            difficulty = parse_tower_difficulty(str(self.difficulty.value))
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tables.handle_tower_redeal(
                self._game_id,
                str(interaction.user.id),
                fiat_amount=fiat,
                tower_difficulty=difficulty,
                interaction=interaction,
            )
        except Exception as exc:
            panel = self._errors.panel_for(exc)
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class BaccaratBetModal(discord.ui.Modal, title="Baccarat bet"):
    amount = discord.ui.TextInput(label="Amount", placeholder="5", max_length=16)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        baccarat_id: str,
        side,
        *,
        hint: str = "",
    ) -> None:
        super().__init__(title=f"Baccarat · {side.label()}"[:45])
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._baccarat_id = baccarat_id
        self._side = side
        if hint:
            self.amount.placeholder = hint[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot

        await interaction.response.defer(ephemeral=True)
        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            table = await self._container.baccarat.place_bet(
                self._baccarat_id, str(interaction.user.id), self._side, fiat
            )
            bot = interaction.client
            if isinstance(bot, CasinoBot):
                await bot.baccarat_tables.on_joined(table)
                panel = self._embeds.baccarat_fairness_lock(table)
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
                panel.stop()
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        await interaction.followup.send(
            **self._embeds.success(
                "Bet placed",
                f"You are on **{self._side.label()}** this round.",
            ).send_kwargs(ephemeral=True)
        )


class JackpotJoinModal(discord.ui.Modal, title="Join jackpot"):
    amount = discord.ui.TextInput(label="Amount", placeholder="5", max_length=16)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        jackpot_id: str,
        *,
        hint: str = "",
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._jackpot_id = jackpot_id
        if hint:
            self.amount.placeholder = hint[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot

        await interaction.response.defer(ephemeral=True)
        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            jackpot = await self._container.jackpots.join(self._jackpot_id, str(interaction.user.id), fiat)
            bot = interaction.client
            if isinstance(bot, CasinoBot):
                await bot.jackpots.on_joined(jackpot)
        except Exception as exc:
            await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        await interaction.followup.send(
            **self._embeds.success("Joined jackpot", "Your amount is in the pot.").send_kwargs(ephemeral=True)
        )


class WagerRacePrizesModal(discord.ui.Modal, title="Wager race prizes"):
    first = discord.ui.TextInput(label="1st place USD", placeholder="100", max_length=16)
    second = discord.ui.TextInput(label="2nd place USD", placeholder="50", max_length=16)
    third = discord.ui.TextInput(label="3rd place USD", placeholder="25", max_length=16)
    fourth_fifth = discord.ui.TextInput(label="4th and 5th place USD each", placeholder="10", max_length=16)

    def __init__(self, container: AppContainer, embeds: EmbedFactory, guild_id: str) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._guild_id = guild_id
        self._errors = DiscordErrorPresenter()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.presentation.config_view import build_admin_config
        from casino.domain.wager_race import parse_prize_usd

        try:
            config = await self._container.guild_configs.set_wager_race_prizes(
                self._guild_id,
                first_usd=parse_prize_usd(str(self.first.value)),
                second_usd=parse_prize_usd(str(self.second.value)),
                third_usd=parse_prize_usd(str(self.third.value)),
                fourth_fifth_usd=parse_prize_usd(str(self.fourth_fifth.value)),
            )
        except Exception as exc:
            await interaction.response.send_message(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
            return
        panel = build_admin_config(self._container, self._embeds, config, page=7)
        await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class BlackjackBetModal(discord.ui.Modal, title="Change bet"):
    amount = discord.ui.TextInput(label="New bet amount", placeholder="5", max_length=16)

    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        game_id: str,
    ) -> None:
        super().__init__()
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._game_id = game_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from casino.discord_bot.bot import CasinoBot

        try:
            fiat = self._container.validation.parse_fiat_amount(str(self.amount.value))
            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            await bot.tables.handle_blackjack_redeal(
                self._game_id,
                str(interaction.user.id),
                fiat_amount=fiat,
                interaction=interaction,
            )
        except Exception as exc:
            panel = self._errors.panel_for(exc)
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


