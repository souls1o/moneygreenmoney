from __future__ import annotations

from decimal import Decimal

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.interactions import ack, send_panel
from casino.domain.rain import parse_rain_requirement_usd
from casino.logging.logger import LogContext, get_logger


class RainCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()
        self._logger = get_logger("casino.discord.rain")

    @app_commands.command(
        name="rain",
        description="Start a rain. Joiners need 10% wager and deposit after it starts, plus the required status.",
    )
    @app_commands.describe(
        amount="Rain pot in your selected currency (minimum $50 USD, admins exempt)",
        duration="How long the rain stays open (10s to 7d). Examples: 30s, 5m, 2h, 1d",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def rain(self, interaction: discord.Interaction, amount: str, duration: str) -> None:
        await ack(interaction)
        await self._container.rate_limits.enforce(f"rain:{interaction.user.id}")
        await self._start_rain(
            interaction,
            amount=amount,
            duration=duration,
            waive_minimum=self._can_waive_minimum(interaction),
        )

    @app_commands.command(
        name="admin_rain",
        description="Start a rain with custom USD wager and deposit requirements.",
    )
    @app_commands.describe(
        amount="Rain pot in your selected currency",
        wager_requirement="USD that joiners must wager after this rain starts",
        deposit_requirement="USD that joiners must deposit after this rain starts",
        duration="How long the rain stays open (10s to 7d). Examples: 30s, 5m, 2h, 1d",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def admin_rain(
        self,
        interaction: discord.Interaction,
        amount: str,
        wager_requirement: str,
        deposit_requirement: str,
        duration: str,
    ) -> None:
        await ack(interaction)
        if not self._can_waive_minimum(interaction):
            await send_panel(
                interaction,
                self._embeds.error("Only admins can start an admin rain."),
                ephemeral=True,
            )
            return
        await self._container.rate_limits.enforce(f"admin_rain:{interaction.user.id}")
        await self._start_rain(
            interaction,
            amount=amount,
            duration=duration,
            waive_minimum=True,
            wager_requirement_usd=parse_rain_requirement_usd(wager_requirement),
            deposit_requirement_usd=parse_rain_requirement_usd(deposit_requirement),
        )

    async def _start_rain(
        self,
        interaction: discord.Interaction,
        *,
        amount: str,
        duration: str,
        waive_minimum: bool,
        wager_requirement_usd: Decimal | None = None,
        deposit_requirement_usd: Decimal | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await send_panel(
                interaction,
                self._embeds.error("Rains can only be started in a server."),
                ephemeral=True,
            )
            return
        user_id = str(interaction.user.id)
        rain_id: str | None = None
        posted = False
        with LogContext.bind(userId=user_id):
            try:
                config = await self._container.guild_configs.get(str(interaction.guild_id))
                if not config.rain_channel_id:
                    await interaction.followup.send(
                        **self._embeds.error(
                            "Ask an admin to set the rain channel in `/admin config`."
                        ).send_kwargs(ephemeral=True)
                    )
                    return
                fiat = self._container.validation.parse_fiat_amount(amount)
                seconds = self._container.validation.parse_duration_seconds(duration)
                rain = await self._container.rains.create_rain(
                    creator_id=user_id,
                    fiat_amount=fiat,
                    duration_seconds=seconds,
                    guild_id=str(interaction.guild_id),
                    waive_minimum=waive_minimum,
                    wager_requirement_usd=wager_requirement_usd,
                    deposit_requirement_usd=deposit_requirement_usd,
                )
                rain_id = rain.rain_id
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if not isinstance(bot, CasinoBot):
                    raise RuntimeError("Casino bot is not ready.")
                refreshed = await bot.rains.provision(rain)
                posted = True
                listing = f"<#{refreshed.channel_id}>" if refreshed.channel_id else "its channel"
                await interaction.followup.send(
                    **self._embeds.success(
                        "Rain started",
                        f"Join it in {listing}.",
                    ).send_kwargs(ephemeral=True)
                )
            except Exception as exc:
                self._logger.exception("rain_command_failed", rainId=rain_id)
                if rain_id is not None and not posted:
                    try:
                        await self._container.rains.abort(rain_id)
                    except Exception:
                        pass
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))

    def _can_waive_minimum(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        role_ids = [role.id for role in member.roles]
        return self._container.permissions.is_admin(member.id, role_ids)
