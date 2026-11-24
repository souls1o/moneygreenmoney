from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.records import WagerRaceRecord
from casino.logging.logger import get_logger


class WagerRacePresenter:
    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.wager_race")

    async def resume(self) -> None:
        for guild in self._bot.guilds:
            config = await self._container.guild_configs.get(str(guild.id))
            if config.wager_race_channel_id:
                await self.ensure(str(guild.id))

    async def ensure(self, guild_id: str, *, channel_id: str | None = None) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        target_channel_id = channel_id or config.wager_race_channel_id
        if not target_channel_id:
            return None
        channel = await self._text_channel(target_channel_id)
        if channel is None:
            return None
        live = await self._container.wager_races.live_for_guild(guild_id)
        message = None
        if live is not None and live.message_id and live.channel_id == str(channel.id):
            try:
                message = await channel.fetch_message(int(live.message_id))
            except discord.HTTPException:
                message = None
        if message is None and config.wager_race_message_id:
            try:
                message = await channel.fetch_message(int(config.wager_race_message_id))
            except discord.HTTPException:
                message = None
        race = await self._container.wager_races.ensure_open(
            guild_id,
            channel_id=str(channel.id),
            message_id=str(message.id) if message is not None else None,
        )
        return await self._publish(channel, race, message)

    async def on_settled(self, race: WagerRaceRecord) -> None:
        await self._edit(race)
        await self.ensure(race.guild_id, channel_id=race.channel_id)

    async def on_open(self, race: WagerRaceRecord) -> None:
        await self._edit(race)

    async def _publish(
        self,
        channel: discord.TextChannel,
        race: WagerRaceRecord,
        message: discord.Message | None,
    ) -> discord.Message:
        panel = self._embeds.wager_race_table(race)
        if message is None:
            message = await channel.send(**panel.send_kwargs())
            panel.stop()
            await self._container.wager_races.attach_listing(race.race_id, str(channel.id), str(message.id))
        else:
            try:
                await message.edit(**panel.edit_kwargs())
                panel.stop()
            except discord.HTTPException:
                panel.stop()
                message = await channel.send(**self._embeds.wager_race_table(race).send_kwargs())
                await self._container.wager_races.attach_listing(race.race_id, str(channel.id), str(message.id))
        await self._container.guild_configs.set_wager_race_message(race.guild_id, str(message.id))
        return message

    async def _edit(self, race: WagerRaceRecord) -> None:
        if not race.channel_id or not race.message_id:
            return
        channel = await self._text_channel(race.channel_id)
        if channel is None:
            return
        panel = self._embeds.wager_race_table(race)
        try:
            message = await channel.fetch_message(int(race.message_id))
            await message.edit(**panel.edit_kwargs())
        except discord.HTTPException:
            self._logger.exception("wager_race_edit_failed", raceId=race.race_id)
        finally:
            panel.stop()

    async def _text_channel(self, channel_id: str) -> discord.TextChannel | None:
        channel = self._bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None
