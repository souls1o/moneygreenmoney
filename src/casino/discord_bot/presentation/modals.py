from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter


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
        super().__init__(title=f"House withdraw · {balance_text}"[:45])
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = errors
        self.amount = discord.ui.TextInput(
            label="Amount in USD",
            placeholder=f"House book {balance_text}",
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

