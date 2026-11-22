from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import BaccaratStatus
from casino.domain.records import BaccaratRecord
from casino.logging.logger import get_logger


class BaccaratPresenter:
    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.baccarat")
        self._scheduled: set[str] = set()
        self._history_posted: set[str] = set()

    async def resume(self) -> None:
        for guild in self._bot.guilds:
            config = await self._container.guild_configs.get(str(guild.id))
            if config.baccarat_channel_id:
                await self.ensure(str(guild.id))
            live = await self._container.baccarat.live_for_guild(str(guild.id))
            if live is not None and live.status is BaccaratStatus.SHOWING:
                await self._post_history(live)

    async def ensure(self, guild_id: str, *, channel_id: str | None = None) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        target_channel_id = channel_id or config.baccarat_channel_id
        if not target_channel_id:
            return None
        channel = await self._text_channel(target_channel_id)
        if channel is None:
            return None
        live = await self._container.baccarat.live_for_guild(guild_id)
        message = None
        if live is not None and live.message_id and live.channel_id == str(channel.id):
            try:
                message = await channel.fetch_message(int(live.message_id))
            except discord.HTTPException:
                message = None
        if message is None and config.baccarat_message_id:
            try:
                message = await channel.fetch_message(int(config.baccarat_message_id))
            except discord.HTTPException:
                message = None
        table = await self._container.baccarat.ensure_open(
            guild_id,
            channel_id=str(channel.id),
            message_id=str(message.id) if message is not None else None,
        )
        panel = self._embeds.baccarat_table(table)
        if message is None:
            message = await channel.send(**panel.send_kwargs())
            panel.stop()
            await self._container.baccarat.attach_listing(table.baccarat_id, str(channel.id), str(message.id))
        else:
            try:
                await message.edit(**panel.edit_kwargs())
                panel.stop()
            except discord.HTTPException:
                panel.stop()
                message = await channel.send(**self._embeds.baccarat_table(table).send_kwargs())
                await self._container.baccarat.attach_listing(table.baccarat_id, str(channel.id), str(message.id))
        await self._container.guild_configs.set_baccarat_message(guild_id, str(message.id))
        refreshed = await self._container.baccarat.get(table.baccarat_id)
        self.schedule(refreshed)
        return message

    def schedule(self, table: BaccaratRecord) -> None:
        if table.baccarat_id in self._scheduled:
            return
        if table.status is BaccaratStatus.OPEN and table.expires_at is not None:
            delay = max((table.expires_at - datetime.now(UTC)).total_seconds(), 0)
            self._scheduled.add(table.baccarat_id)
            self._bot.loop.create_task(self._settle_after(table.baccarat_id, delay))
            return
        if table.status is BaccaratStatus.SHOWING and table.reveal_until is not None:
            delay = max((table.reveal_until - datetime.now(UTC)).total_seconds(), 0)
            self._scheduled.add(table.baccarat_id)
            self._bot.loop.create_task(self._reset_after(table.baccarat_id, delay))

    async def on_joined(self, table: BaccaratRecord) -> None:
        await self._refresh(table)
        self.schedule(table)

    async def on_settled(self, table: BaccaratRecord) -> None:
        self._scheduled.discard(table.baccarat_id)
        await self._refresh(table)
        await self._post_history(table)
        self.schedule(table)

    async def on_reset(self, table: BaccaratRecord) -> None:
        self._scheduled.discard(table.baccarat_id)
        await self._refresh(table)
        self.schedule(table)

    async def _settle_after(self, baccarat_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            table = await self._container.baccarat.settle(baccarat_id)
            await self.on_settled(table)
        except Exception:
            self._logger.exception("baccarat_scheduled_settle_failed", baccaratId=baccarat_id)
        finally:
            self._scheduled.discard(baccarat_id)

    async def _reset_after(self, baccarat_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            table = await self._container.baccarat.reset_after_reveal(baccarat_id)
            await self.on_reset(table)
        except Exception:
            self._logger.exception("baccarat_scheduled_reset_failed", baccaratId=baccarat_id)
        finally:
            self._scheduled.discard(baccarat_id)

    async def _refresh(self, table: BaccaratRecord) -> None:
        if not table.channel_id or not table.message_id:
            return
        channel = await self._text_channel(table.channel_id)
        if channel is None:
            return
        panel = self._embeds.baccarat_table(table)
        try:
            message = await channel.fetch_message(int(table.message_id))
            await message.edit(**panel.edit_kwargs())
            panel.stop()
        except discord.HTTPException:
            self._logger.exception("baccarat_listing_edit_failed", baccaratId=table.baccarat_id)
            panel.stop()

    async def _post_history(self, table: BaccaratRecord) -> None:
        if table.history_message_id or table.baccarat_id in self._history_posted:
            return
        if table.status not in {BaccaratStatus.SHOWING, BaccaratStatus.COMPLETED}:
            return
        if not table.guild_id:
            return
        config = await self._container.guild_configs.get(table.guild_id)
        history_channel_id = config.baccarat_history_channel_id
        if not history_channel_id:
            return
        channel = await self._messageable(history_channel_id)
        if channel is None:
            self._logger.warning(
                "baccarat_history_channel_missing",
                guildId=table.guild_id,
                channelId=history_channel_id,
            )
            return
        panel = self._embeds.baccarat_table(table, history=True)
        try:
            posted = await channel.send(**panel.send_kwargs())
            panel.stop()
            self._history_posted.add(table.baccarat_id)
            await self._container.baccarat.attach_history(table.baccarat_id, str(posted.id))
            table.history_message_id = str(posted.id)
        except discord.HTTPException:
            self._logger.exception("baccarat_history_post_failed", baccaratId=table.baccarat_id)
            panel.stop()

    async def _text_channel(self, channel_id: str) -> discord.TextChannel | None:
        fetched = await self._messageable(channel_id)
        return fetched if isinstance(fetched, discord.TextChannel) else None

    async def _messageable(self, channel_id: str) -> discord.abc.Messageable | None:
        fetched = self._bot.get_channel(int(channel_id))
        if fetched is None:
            try:
                fetched = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return fetched if isinstance(fetched, discord.abc.Messageable) else None
