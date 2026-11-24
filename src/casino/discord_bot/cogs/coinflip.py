from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.game_setup import open_game_setup
from casino.discord_bot.presentation.interactions import ack
from casino.domain.enums import GameType


class CoinflipCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="coinflip",
        description="Create a PvP coinflip. Amount is in your settings currency.",
    )
    @app_commands.describe(amount="Bet amount in your selected currency (for example 5 for 5 EUR)")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def coinflip(self, interaction: discord.Interaction, amount: str) -> None:
        await ack(interaction)
        await self._container.rate_limits.enforce(f"coinflip:{interaction.user.id}")
        await open_game_setup(
            interaction,
            self._container,
            self._embeds,
            self._errors,
            game_type=GameType.COINFLIP,
            amount=amount,
        )
