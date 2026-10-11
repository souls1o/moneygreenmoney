from __future__ import annotations

from casino.discord_bot.presentation.builder import CasinoPanel, EmbedBuilder
from casino.errors import AppError


class DiscordErrorPresenter:
    def panel_for(self, error: BaseException) -> CasinoPanel:
        if isinstance(error, AppError):
            message = str(error) or error.user_message
        else:
            message = "Something went wrong. Please try again."
        title = "Action failed" if isinstance(error, AppError) else "Unexpected error"
        return EmbedBuilder().title(title).description(message).build()

    def embed_for(self, error: BaseException) -> CasinoPanel:
        return self.panel_for(error)

    def message_for(self, error: BaseException) -> str:
        if isinstance(error, AppError):
            return str(error) or error.user_message
        return "Something went wrong. Please try again."
