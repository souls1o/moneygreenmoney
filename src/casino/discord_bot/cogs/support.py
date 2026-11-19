from __future__ import annotations

import discord
from discord.ext import commands

from casino.container import AppContainer


class SupportCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container

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
