from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import JackpotStatus
from casino.domain.records import JackpotRecord
from casino.logging.logger import get_logger


class JackpotPresenter:
    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.jackpot")
        self._scheduled: set[str] = set()
        self._history_posted: set[str] = set()

    async def resume(self) -> None:
        for guild in self._bot.guilds:
            config = await self._container.guild_configs.get(str(guild.id))
            if config.jackpot_channel_id:
                await self.ensure(str(guild.id))

    async def ensure(self, guild_id: str, *, channel_id: str | None = None) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        target_channel_id = channel_id or config.jackpot_channel_id
        if not target_channel_id:
            return None
        channel = await self._text_channel(target_channel_id)
        if channel is None:
            return None
        live = await self._container.jackpots.live_for_guild(guild_id)
        message = None
        if live is not None and live.message_id and live.channel_id == str(channel.id):
            try:
                message = await channel.fetch_message(int(live.message_id))
            except discord.HTTPException:
                message = None
        if message is None and config.jackpot_message_id:
            try:
                message = await channel.fetch_message(int(config.jackpot_message_id))
            except discord.HTTPException:
                message = None
        jackpot = await self._container.jackpots.ensure_open(
            guild_id,
            channel_id=str(channel.id),
            message_id=str(message.id) if message is not None else None,
        )
        panel = self._embeds.jackpot_table(jackpot)
        if message is None:
            message = await channel.send(**panel.send_kwargs())
            panel.stop()
            await self._container.jackpots.attach_listing(jackpot.jackpot_id, str(channel.id), str(message.id))
        else:
            try:
                await message.edit(**panel.edit_kwargs())
                panel.stop()
            except discord.HTTPException:
                panel.stop()
                message = await channel.send(**self._embeds.jackpot_table(jackpot).send_kwargs())
                await self._container.jackpots.attach_listing(jackpot.jackpot_id, str(channel.id), str(message.id))
        await self._container.guild_configs.set_jackpot_message(guild_id, str(message.id))
        refreshed = await self._container.jackpots.get(jackpot.jackpot_id)
        self.schedule(refreshed)
        return message

    def schedule(self, jackpot: JackpotRecord) -> None:
        if jackpot.jackpot_id in self._scheduled:
            return
        if jackpot.status is JackpotStatus.OPEN and jackpot.expires_at is not None:
            delay = max((jackpot.expires_at - datetime.now(UTC)).total_seconds(), 0)
            self._scheduled.add(jackpot.jackpot_id)
            self._bot.loop.create_task(self._settle_after(jackpot.jackpot_id, delay))
            return
        if jackpot.status is JackpotStatus.SHOWING and jackpot.reveal_until is not None:
            delay = max((jackpot.reveal_until - datetime.now(UTC)).total_seconds(), 0)
            self._scheduled.add(jackpot.jackpot_id)
            self._bot.loop.create_task(self._reset_after(jackpot.jackpot_id, delay))

    async def on_joined(self, jackpot: JackpotRecord) -> None:
        await self._refresh(jackpot)
        self.schedule(jackpot)

    async def on_settled(self, jackpot: JackpotRecord) -> None:
        self._scheduled.discard(jackpot.jackpot_id)
        await self._refresh(jackpot)
        await self._post_history(jackpot)
        self.schedule(jackpot)

    async def on_reset(self, jackpot: JackpotRecord) -> None:
        self._scheduled.discard(jackpot.jackpot_id)
        await self._refresh(jackpot)
        self.schedule(jackpot)

    async def _settle_after(self, jackpot_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            jackpot = await self._container.jackpots.settle(jackpot_id)
            await self.on_settled(jackpot)
        except Exception:
            self._logger.exception("jackpot_scheduled_settle_failed", jackpotId=jackpot_id)
        finally:
            self._scheduled.discard(jackpot_id)

    async def _reset_after(self, jackpot_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            jackpot = await self._container.jackpots.reset_after_reveal(jackpot_id)
            await self.on_reset(jackpot)
        except Exception:
            self._logger.exception("jackpot_scheduled_reset_failed", jackpotId=jackpot_id)
        finally:
            self._scheduled.discard(jackpot_id)

    async def _refresh(self, jackpot: JackpotRecord) -> None:
        if not jackpot.channel_id or not jackpot.message_id:
            return
        channel = await self._text_channel(jackpot.channel_id)
        if channel is None:
            return
        panel = self._embeds.jackpot_table(jackpot)
        try:
            message = await channel.fetch_message(int(jackpot.message_id))
            await message.edit(**panel.edit_kwargs())
            panel.stop()
        except discord.HTTPException:
            self._logger.exception("jackpot_listing_edit_failed", jackpotId=jackpot.jackpot_id)
            panel.stop()

    async def _post_history(self, jackpot: JackpotRecord) -> None:
        if jackpot.jackpot_id in self._history_posted:
            return
        if jackpot.status is not JackpotStatus.SHOWING or not jackpot.guild_id:
            return
        config = await self._container.guild_configs.get(jackpot.guild_id)
        if not config.jackpot_history_channel_id:
            return
        channel = await self._text_channel(config.jackpot_history_channel_id)
        if channel is None:
            return
        panel = self._embeds.jackpot_table(jackpot, history=True)
        try:
            await channel.send(**panel.send_kwargs())
            panel.stop()
            self._history_posted.add(jackpot.jackpot_id)
        except discord.HTTPException:
            self._logger.exception("jackpot_history_post_failed", jackpotId=jackpot.jackpot_id)
            panel.stop()

    async def _text_channel(self, channel_id: str) -> discord.TextChannel | None:
        fetched = self._bot.get_channel(int(channel_id))
        if fetched is None:
            try:
                fetched = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return fetched if isinstance(fetched, discord.TextChannel) else None
