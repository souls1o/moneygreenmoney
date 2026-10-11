from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import RainStatus
from casino.domain.records import RainRecord
from casino.errors import RainError
from casino.logging.logger import get_logger


class RainPresenter:
    """Posts and updates rain join embeds in the configured rain channel."""

    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.rain")
        self._scheduled: set[str] = set()

    async def provision(self, rain: RainRecord) -> RainRecord:
        if not rain.guild_id:
            raise RainError("Rains can only be started in a server.")
        config = await self._container.guild_configs.get(rain.guild_id)
        if not config.rain_channel_id:
            raise RainError("Ask an admin to set the rain channel in `/admin config`.")
        channel = await self._text_channel(config.rain_channel_id)
        if channel is None:
            raise RainError("The rain channel is missing. Ask an admin to set it again.")
        panel = self._embeds.rain_listing(rain, ping_role_id=config.rain_ping_role_id)
        message = await channel.send(**panel.send_kwargs())
        panel.stop()
        await self._container.rains.attach_listing(rain.rain_id, str(channel.id), str(message.id))
        refreshed = await self._container.rains.get(rain.rain_id)
        self.schedule_settle(refreshed)
        return refreshed

    async def resume(self) -> None:
        for rain in await self._container.rains.list_open():
            self.schedule_settle(rain)

    def schedule_settle(self, rain: RainRecord) -> None:
        if rain.rain_id in self._scheduled or rain.expires_at is None:
            return
        if rain.status is not RainStatus.PENDING:
            return
        self._scheduled.add(rain.rain_id)
        delay = max((rain.expires_at - datetime.now(UTC)).total_seconds(), 0)
        self._bot.loop.create_task(self._settle_after(rain.rain_id, delay))

    async def on_joined(self, rain: RainRecord) -> None:
        await self._edit_listing(rain)

    async def on_settled(self, rain: RainRecord) -> None:
        self._scheduled.discard(rain.rain_id)
        await self._edit_listing(rain)

    async def _settle_after(self, rain_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            rain = await self._container.rains.settle(rain_id)
            await self.on_settled(rain)
        except Exception:
            self._logger.exception("rain_scheduled_settle_failed", rainId=rain_id)
        finally:
            self._scheduled.discard(rain_id)

    async def _edit_listing(self, rain: RainRecord) -> None:
        if not rain.channel_id or not rain.message_id:
            return
        channel = await self._text_channel(rain.channel_id)
        if channel is None:
            return
        ping_role_id = None
        if rain.guild_id:
            config = await self._container.guild_configs.get(rain.guild_id)
            ping_role_id = config.rain_ping_role_id
        panel = self._embeds.rain_listing(rain, ping_role_id=ping_role_id)
        try:
            message = await channel.fetch_message(int(rain.message_id))
            await message.edit(**panel.edit_kwargs())
            panel.stop()
        except discord.HTTPException:
            self._logger.exception("rain_listing_edit_failed", rainId=rain.rain_id)
            panel.stop()

    async def _text_channel(self, channel_id: str) -> discord.TextChannel | None:
        channel = self._bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None
