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
from casino.logging.logger import LogContext


class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="dice",
        description="Create a first-to dice table. Amount is in your settings currency.",
    )
    @app_commands.describe(amount="Bet amount in your selected currency (for example 5 for 5 EUR)")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def dice(self, interaction: discord.Interaction, amount: str) -> None:
        await ack(interaction)
        await self._container.rate_limits.enforce(f"dice:{interaction.user.id}")
        await open_game_setup(
            interaction,
            self._container,
            self._embeds,
            self._errors,
            game_type=GameType.DICE,
            amount=amount,
        )

    @app_commands.command(name="roll", description="Roll 2 dice on your turn in the active dice table.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def roll(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        await self._container.rate_limits.enforce(f"roll:{interaction.user.id}")
        channel_id = str(interaction.channel_id) if interaction.channel_id else ""
        with LogContext.bind(userId=str(interaction.user.id)):
            try:
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if isinstance(bot, CasinoBot):
                    result = await bot.tables.handle_player_roll(channel_id, str(interaction.user.id))
                else:
                    result = await self._container.games.roll_dice(channel_id, str(interaction.user.id))
                turn = result.game.current_turn_id()
                if result.game.status.value == "completed":
                    message = "The match is over."
                elif turn:
                    message = f"Next turn: <@{turn}>" if not turn.startswith("HOUSE") else "House is rolling."
                else:
                    message = "Roll recorded."
                await interaction.followup.send(
                    **self._embeds.success("Rolled", message).send_kwargs(ephemeral=True)
                )
            except Exception as exc:
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
