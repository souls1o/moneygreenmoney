from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.records import GameRecord, is_house_seat
from casino.logging.logger import get_logger


class GameNotificationPresenter:
    """Sends private embed DMs for table events. Failures never block the game."""

    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.notify")

    async def player_joined(self, game: GameRecord, joiner: discord.abc.User) -> None:
        joiner_id = str(joiner.id)
        if is_house_seat(joiner_id):
            return
        recipients = [player_id for player_id in game.human_player_ids() if player_id != joiner_id]
        if not recipients:
            return
        for player_id in recipients:
            await self._dm(player_id, self._embeds.player_joined(game, joiner_id), game.game_id)

    async def _dm(self, user_id: str, panel, game_id: str) -> None:
        try:
            user = self._bot.get_user(int(user_id)) or await self._bot.fetch_user(int(user_id))
            await user.send(**panel.send_kwargs())
        except (discord.HTTPException, discord.Forbidden, ValueError):
            self._logger.info("join_dm_skipped", userId=user_id, gameId=game_id)
        except Exception:
            self._logger.exception("join_dm_failed", userId=user_id, gameId=game_id)
        finally:
            panel.stop()
