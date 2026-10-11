from __future__ import annotations

import discord
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.logging.logger import get_logger


class VerificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.verify")

    async def ensure_panel(self, guild_id: str) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        if not config.verify_channel_id:
            return None
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return None
        channel = guild.get_channel(int(config.verify_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return None
        panel = self._embeds.verify_hub()
        if config.verify_message_id:
            try:
                existing = await channel.fetch_message(int(config.verify_message_id))
                await existing.edit(**panel.edit_kwargs())
                panel.stop()
                return existing
            except discord.HTTPException:
                self._logger.info("verify_panel_repost", guildId=guild_id)
        posted = await channel.send(**panel.send_kwargs())
        panel.stop()
        await self._container.guild_configs.set_verify_message(guild_id, str(posted.id))
        return posted
