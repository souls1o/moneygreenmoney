from __future__ import annotations

from casino.domain.enums import GameType, GuildConfigSlot
from casino.domain.records import GuildConfigRecord
from casino.logging.logger import get_logger
from casino.repositories.guild_config_repository import GuildConfigRepository
from casino.utils.clock import Clock


class GuildConfigService:
    def __init__(self, repository: GuildConfigRepository, clock: Clock) -> None:
        self._configs = repository
        self._clock = clock
        self._logger = get_logger("casino.guild_config")

    async def get(self, guild_id: str) -> GuildConfigRecord:
        return await self._configs.get(guild_id)

    async def set_slot(
        self, guild_id: str, slot: GuildConfigSlot, channel_id: str | None
    ) -> GuildConfigRecord:
        config = await self._configs.set_slot(guild_id, slot, channel_id, self._clock.now())
        self._logger.info(
            "guild_config_updated",
            guildId=guild_id,
            slot=slot.value,
            channelId=channel_id,
        )
        return config

    async def outcomes_channel_id(self, guild_id: str, game_type: GameType) -> str | None:
        config = await self.get(guild_id)
        return config.outcomes_channel_id(game_type)

    async def category_id(self, guild_id: str, game_type: GameType) -> str | None:
        config = await self.get(guild_id)
        return config.category_id(game_type)

    async def take_next_table_number(self, guild_id: str) -> int:
        number = await self._configs.take_next_table_number(guild_id)
        self._logger.info("table_number_allocated", guildId=guild_id, tableNumber=number)
        return number

    async def set_pf_message(self, guild_id: str, message_id: str) -> GuildConfigRecord:
        config = await self._configs.set_pf_message(guild_id, message_id, self._clock.now())
        self._logger.info("provably_fair_panel_stored", guildId=guild_id, messageId=message_id)
        return config

    async def set_verify_message(self, guild_id: str, message_id: str) -> GuildConfigRecord:
        config = await self._configs.set_verify_message(guild_id, message_id, self._clock.now())
        self._logger.info("verify_panel_stored", guildId=guild_id, messageId=message_id)
        return config
