from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.invest_view import build_invest_panel, require_investor_role
from casino.logging.logger import LogContext


class InvestCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(name="invest", description="Buy or sell a share of the house book")
    @app_commands.default_permissions()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def invest(self, interaction: discord.Interaction) -> None:
        await self._container.rate_limits.enforce(f"invest:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            await require_investor_role(interaction, self._container)
            position = await self._container.investors.position(str(interaction.user.id))
            quote = await self._container.price.get_quote(allow_stale=True)
            user = await self._container.users.get_or_create(str(interaction.user.id))
            panel = build_invest_panel(
                self._container,
                self._embeds,
                self._errors,
                interaction.user,
                user,
                position,
                quote,
            )
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
