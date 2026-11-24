from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.game_setup import start_limbo
from casino.discord_bot.presentation.interactions import ack, send_panel
from casino.domain.limbo import parse_limbo_multiplier
from casino.logging.logger import LogContext


class LimboCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="limbo",
        description="Target a multiplier vs the house. Amount is in your settings currency.",
    )
    @app_commands.describe(
        amount="Bet amount in your selected currency (for example 5 for 5 EUR)",
        multiplier="Target multiplier (for example 2 or 2.50)",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def limbo(self, interaction: discord.Interaction, amount: str, multiplier: str) -> None:
        await ack(interaction, ephemeral=False)
        await self._container.rate_limits.enforce(f"limbo:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            try:
                fiat = self._container.validation.parse_fiat_amount(amount)
                target = parse_limbo_multiplier(multiplier)
            except Exception as exc:
                await send_panel(interaction, self._errors.panel_for(exc), ephemeral=True)
                return
            await start_limbo(
                interaction,
                self._container,
                self._embeds,
                self._errors,
                user_id=str(interaction.user.id),
                fiat=fiat,
                multiplier=target,
            )
