from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.settings_view import build_settings_panel
from casino.logging.logger import LogContext


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(name="settings", description="Set your betting currency")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def settings(self, interaction: discord.Interaction) -> None:
        await self._container.rate_limits.enforce(f"settings:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            user = await self._container.users.get_or_create(str(interaction.user.id))
            currencies = self._container.settings_service.supported_currencies()
            panel = build_settings_panel(
                self._container,
                self._embeds,
                self._errors,
                user,
                currencies,
            )
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
