from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.logging.logger import LogContext


class SupportCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(name="support", description="Open a private support ticket")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def support(self, interaction: discord.Interaction) -> None:
        await self._container.rate_limits.enforce(f"support:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    **self._embeds.error("Use /support in the casino server.").send_kwargs(ephemeral=True)
                )
                return
            config = await self._container.guild_configs.get(str(interaction.guild_id))
            if not config.support_category_id:
                await interaction.response.send_message(
                    **self._embeds.error(
                        "Ask an admin to set the support category in `/admin config`."
                    ).send_kwargs(ephemeral=True)
                )
                return
            panel = self._embeds.support_hub()
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
            panel.stop()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._record_chat(lambda tickets: tickets.record_message(message))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self._record_chat(lambda tickets: tickets.record_edit(before, after))

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.cached_message is not None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            after = payload.cached_message or await channel.fetch_message(payload.message_id)
        except discord.HTTPException:
            return
        await self._record_chat(lambda tickets: tickets.record_edit(None, after))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self._record_chat(
            lambda tickets: tickets.record_delete(
                payload.cached_message,
                channel_id=payload.channel_id,
                message_id=payload.message_id,
            )
        )

    async def _record_chat(self, action) -> None:
        from casino.discord_bot.bot import CasinoBot

        if not isinstance(self.bot, CasinoBot):
            return
        try:
            await action(self.bot.tickets)
        except Exception:
            return
