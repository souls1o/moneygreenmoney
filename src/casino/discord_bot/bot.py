from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.cogs.admin import AdminCog
from casino.discord_bot.cogs.affiliate import AffiliateCog
from casino.discord_bot.cogs.balance import BalanceCog
from casino.discord_bot.cogs.blackjack import BlackjackCog
from casino.discord_bot.cogs.coinflip import CoinflipCog
from casino.discord_bot.cogs.dice import DiceCog
from casino.discord_bot.cogs.invest import InvestCog
from casino.discord_bot.cogs.limbo import LimboCog
from casino.discord_bot.cogs.target import TargetCog
from casino.discord_bot.cogs.profile import ProfileCog
from casino.discord_bot.cogs.rain import RainCog
from casino.discord_bot.cogs.settings import SettingsCog
from casino.discord_bot.cogs.support import SupportCog
from casino.discord_bot.cogs.verification import VerificationCog
from casino.discord_bot.presentation.admin_alerts import AdminChannelPresenter
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.notifications import GameNotificationPresenter
from casino.discord_bot.presentation.jackpot import JackpotPresenter
from casino.discord_bot.presentation.rain import RainPresenter
from casino.discord_bot.presentation.table import GameTablePresenter
from casino.discord_bot.presentation.tickets import TicketPresenter
from casino.logging.logger import get_logger


class CasinoBot(commands.Bot):
    def __init__(self, container: AppContainer) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.tree.allowed_installs = app_commands.AppInstallationType(guild=True, user=False)
        self.tree.allowed_contexts = app_commands.AppCommandContext(
            guild=True, dm_channel=False, private_channel=False
        )
        self.container = container
        self.notifications = GameNotificationPresenter(self, container)
        self.admin_alerts = AdminChannelPresenter(self, container)
        self.tables = GameTablePresenter(self, container, self.notifications, self.admin_alerts)
        self.rains = RainPresenter(self, container)
        self.jackpots = JackpotPresenter(self, container)
        self.tickets = TicketPresenter(self, container)
        container.deposits.set_listeners(
            on_credited=self.admin_alerts.on_player_deposit,
            on_house_credited=self.admin_alerts.on_house_deposit,
        )
        container.withdrawals.set_listener(self.admin_alerts.on_withdrawal)
        self._errors = DiscordErrorPresenter()
        self._logger = get_logger("casino.discord")

    async def setup_hook(self) -> None:
        from casino.discord_bot.cogs.router import ComponentRouterCog

        self.container.expiry_worker.set_listener(self.tables.on_resolved)
        self.container.expiry_worker.set_channel_listener(self.tables.on_table_cleared)
        self.container.rain_worker.set_listener(self.rains.on_settled)
        self.container.jackpot_worker.set_listeners(
            on_settled=self.jackpots.on_settled,
            on_reset=self.jackpots.on_reset,
        )
        await self.add_cog(BalanceCog(self, self.container))
        await self.add_cog(CoinflipCog(self, self.container))
        await self.add_cog(DiceCog(self, self.container))
        await self.add_cog(BlackjackCog(self, self.container))
        await self.add_cog(TargetCog(self, self.container))
        await self.add_cog(LimboCog(self, self.container))
        await self.add_cog(ProfileCog(self, self.container))
        await self.add_cog(AffiliateCog(self, self.container))
        await self.add_cog(InvestCog(self, self.container))
        await self.add_cog(SettingsCog(self, self.container))
        await self.add_cog(AdminCog(self, self.container))
        await self.add_cog(RainCog(self, self.container))
        await self.add_cog(VerificationCog(self, self.container))
        await self.add_cog(SupportCog(self, self.container))
        await self.add_cog(ComponentRouterCog(self, self.container))
        self.tree.on_error = self.on_app_command_error
        global_synced = await self.tree.sync()
        guild_id = self.container.settings.discord_guild_id
        guild_count = 0
        if guild_id:
            guild = discord.Object(id=guild_id)
            # Guild copies of the same global commands show twice in the home server.
            self.tree.clear_commands(guild=guild)
            guild_count = len(await self.tree.sync(guild=guild))
        self._logger.info(
            "commands_synced",
            globalCount=len(global_synced),
            guildCount=guild_count,
        )

    async def on_ready(self) -> None:
        self._logger.info("discord_ready", user=str(self.user), userId=str(self.user.id) if self.user else None)
        try:
            await self.rains.resume()
        except Exception:
            self._logger.exception("rain_resume_failed")
        try:
            await self.jackpots.resume()
        except Exception:
            self._logger.exception("jackpot_resume_failed")
        for guild in self.guilds:
            try:
                await self.sync_invest_command_access(guild)
            except Exception:
                self._logger.exception("invest_command_access_failed", guildId=str(guild.id))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            await self.sync_invest_command_access(guild)
        except Exception:
            self._logger.exception("invest_command_access_failed", guildId=str(guild.id))

    async def sync_invest_command_access(self, guild: discord.Guild) -> None:
        if self.application_id is None:
            return
        try:
            commands = await self.tree.fetch_commands()
        except discord.HTTPException:
            self._logger.exception("invest_command_fetch_failed")
            return
        command = discord.utils.get(commands, name="invest")
        if command is None:
            return
        config = await self.container.guild_configs.get(str(guild.id))
        permissions: list[dict[str, object]] = []
        if config.investor_role_id:
            permissions.append(
                {
                    "id": int(config.investor_role_id),
                    "type": int(discord.AppCommandPermissionType.role),
                    "permission": True,
                }
            )
        try:
            await self.http.edit_application_command_permissions(
                self.application_id,
                guild.id,
                command.id,
                {"permissions": permissions},
            )
        except discord.Forbidden:
            self._logger.warning(
                "invest_command_permissions_forbidden",
                guildId=str(guild.id),
                hint="Allow /invest for the Investor role in Server Settings → Integrations",
            )
        except discord.HTTPException:
            self._logger.exception("invest_command_permissions_failed", guildId=str(guild.id))

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        root = error.original if isinstance(error, discord.app_commands.CommandInvokeError) else error
        self._logger.warning("command_error", error=str(root), userId=str(interaction.user.id))
        panel = self._errors.panel_for(root)
        if interaction.response.is_done():
            await interaction.followup.send(**panel.send_kwargs(ephemeral=True))
        else:
            await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))
