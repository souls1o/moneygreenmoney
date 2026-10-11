from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.logging.logger import LogContext


class AffiliateCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)

    @app_commands.command(name="affiliate", description="Lock a referrer or view affiliate stats")
    @app_commands.describe(member="Player to lock as your referrer. This cannot be changed.")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def affiliate(
        self,
        interaction: discord.Interaction,
        member: discord.User | None = None,
    ) -> None:
        await self._container.rate_limits.enforce(f"affiliate:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            if member is not None:
                await self._container.affiliates.bind(str(interaction.user.id), str(member.id))
            stats = await self._container.affiliates.stats(str(interaction.user.id))
            bound = member is not None
            panel = self._embeds.affiliate(interaction.user, stats, just_bound=bound)
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
            panel.stop()
