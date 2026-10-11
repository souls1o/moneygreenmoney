from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.logging.logger import LogContext


class BalanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(name="balance", description="Show your SOL balance and deposit options")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def balance(self, interaction: discord.Interaction) -> None:
        await self._container.rate_limits.enforce(f"balance:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            user = await self._container.users.get_or_create(str(interaction.user.id))
            quote = await self._container.price.get_quote(allow_stale=True)
            panel = self._embeds.balance(user, quote)
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
            panel.stop()

    @app_commands.command(name="tip", description="Send SOL from your balance to another player")
    @app_commands.describe(
        member="Player to tip",
        amount="Amount in your selected currency (for example 5 for 5 EUR)",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def tip(self, interaction: discord.Interaction, member: discord.Member, amount: str) -> None:
        await self._container.rate_limits.enforce(f"tip:{interaction.user.id}")
        if member.bot:
            await interaction.response.send_message(
                **self._embeds.error("You can only tip players.").send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id)):
            try:
                fiat = self._container.validation.parse_fiat_amount(amount)
                sender = await self._container.users.get_or_create(str(interaction.user.id))
                quote = await self._container.price.get_quote(allow_stale=True)
                sol_amount = self._container.math.fiat_to_sol(fiat, quote.sol_price(sender.display_currency))
                result = await self._container.tips.send(str(interaction.user.id), str(member.id), sol_amount)
            except Exception as exc:
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
                return
            currency = sender.display_currency
            await interaction.followup.send(
                **self._embeds.success(
                    "Tip sent",
                    (
                        f"Sent {self._container.math.format_fiat_symbol(fiat, currency)} "
                        f"(`{self._container.math.format_sol(result.amount)} SOL`) to {member.mention}."
                    ),
                ).send_kwargs(ephemeral=True)
            )
