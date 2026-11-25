from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.game_setup import start_tower
from casino.discord_bot.presentation.interactions import ack, send_panel
from casino.domain.tower import parse_tower_difficulty
from casino.logging.logger import LogContext


class TowerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="tower",
        description="Climb floors vs the house. Amount is in your settings currency.",
    )
    @app_commands.describe(
        amount="Bet amount in your selected currency (for example 5 for 5 EUR)",
        difficulty="Easy 4 tiles, Medium 3, Hard 2. One bomb per floor.",
    )
    @app_commands.choices(
        difficulty=[
            app_commands.Choice(name="Easy", value="easy"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Hard", value="hard"),
        ]
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def tower(
        self,
        interaction: discord.Interaction,
        amount: str,
        difficulty: app_commands.Choice[str],
    ) -> None:
        await ack(interaction)
        await self._container.rate_limits.enforce(f"tower:{interaction.user.id}")
        with LogContext.bind(userId=str(interaction.user.id)):
            try:
                fiat = self._container.validation.parse_fiat_amount(amount)
                parsed = parse_tower_difficulty(difficulty.value)
            except Exception as exc:
                await send_panel(interaction, self._errors.panel_for(exc), ephemeral=True)
                return
            await start_tower(
                interaction,
                self._container,
                self._embeds,
                self._errors,
                user_id=str(interaction.user.id),
                fiat=fiat,
                difficulty=parsed,
            )
