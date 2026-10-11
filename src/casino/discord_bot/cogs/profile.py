from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.logging.logger import LogContext


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)

    @app_commands.command(name="profile", description="View casino stats and game history")
    @app_commands.describe(member="Another player to inspect")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        await self._container.rate_limits.enforce(f"profile:{interaction.user.id}")
        with LogContext.bind(userId=str(target.id)):
            user = await self._container.users.get_or_create(str(target.id))
            stats = await self._container.games.profile_stats(str(target.id))
            panel = self._embeds.profile(target, user, stats)
            await interaction.response.send_message(**panel.send_kwargs())
            panel.stop()
