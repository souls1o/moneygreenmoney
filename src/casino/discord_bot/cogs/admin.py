from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.builder import CasinoPanel
from casino.discord_bot.presentation.config_view import build_admin_config
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import GameType, GuildConfigSlot, UserRole
from casino.logging.logger import LogContext
from casino.utils.money import SolAmount


class AdminCog(commands.Cog):
    admin = app_commands.Group(
        name="admin",
        description="House administration",
        default_permissions=discord.Permissions(administrator=True),
        allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False),
    )

    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)

    def _role_ids(self, interaction: discord.Interaction) -> list[int]:
        if isinstance(interaction.user, discord.Member):
            return [role.id for role in interaction.user.roles]
        return []

    async def _guard(self, interaction: discord.Interaction) -> None:
        self._container.permissions.require_admin(interaction.user.id, self._role_ids(interaction))

    @admin.command(name="credit", description="Credit SOL to a player")
    @app_commands.describe(member="Player", amount_sol="SOL amount", reason="Ledger reason")
    async def credit(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount_sol: str,
        reason: str,
    ) -> None:
        await self._guard(interaction)
        with LogContext.bind(userId=str(member.id)):
            user = await self._container.admin.adjust(
                target_discord_id=str(member.id),
                amount=SolAmount.parse(amount_sol),
                credit=True,
                reason=reason,
                actor_id=str(interaction.user.id),
            )
            await interaction.response.send_message(
                **self._embeds.success(
                    "Credited",
                    f"{member.mention} now has `{self._container.math.format_sol(user.sol_balance)} SOL`.",
                ).send_kwargs(ephemeral=True)
            )

    @admin.command(name="debit", description="Debit SOL from a player")
    @app_commands.describe(member="Player", amount_sol="SOL amount", reason="Ledger reason")
    async def debit(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount_sol: str,
        reason: str,
    ) -> None:
        await self._guard(interaction)
        with LogContext.bind(userId=str(member.id)):
            user = await self._container.admin.adjust(
                target_discord_id=str(member.id),
                amount=SolAmount.parse(amount_sol),
                credit=False,
                reason=reason,
                actor_id=str(interaction.user.id),
            )
            await interaction.response.send_message(
                **self._embeds.success(
                    "Debited",
                    f"{member.mention} now has `{self._container.math.format_sol(user.sol_balance)} SOL`.",
                ).send_kwargs(ephemeral=True)
            )

    @admin.command(name="cancel", description="Force-cancel an open coinflip")
    @app_commands.describe(game_id="Coinflip game ID")
    async def cancel(self, interaction: discord.Interaction, game_id: str) -> None:
        await self._guard(interaction)
        await self._container.admin.cancel_game(game_id.strip(), str(interaction.user.id))
        await interaction.response.send_message(
            **self._embeds.success("Cancelled", f"Game `{game_id}` was refunded.").send_kwargs(ephemeral=True)
        )

    @admin.command(name="expire", description="Auto-flip full tables that have waited an hour")
    async def expire(self, interaction: discord.Interaction) -> None:
        await self._guard(interaction)
        count = await self._container.admin.expire_games()
        await interaction.response.send_message(
            **self._embeds.success("Auto-flip pass", f"Resolved `{count}` full tables.").send_kwargs(ephemeral=True)
        )

    @admin.command(name="house", description="Show house treasury")
    async def house(self, interaction: discord.Interaction) -> None:
        await self._guard(interaction)
        treasury = await self._container.admin.house_treasury()
        await interaction.response.send_message(
            **self._embeds.house(
                treasury.sol_balance_lamports,
                treasury.total_fees_collected_lamports,
            ).send_kwargs(ephemeral=True)
        )

    @app_commands.command(name="housestats", description="Show house balance, super wallet, and timeframe stats")
    @app_commands.default_permissions(administrator=True)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def housestats(self, interaction: discord.Interaction) -> None:
        await self._guard(interaction)
        report = await self._container.house_stats.snapshot()
        await interaction.response.send_message(**self._embeds.house_stats(report).send_kwargs(ephemeral=True))

    @admin.command(name="fund-house", description="Add SOL to house reserves for call-bot")
    @app_commands.describe(amount_sol="SOL amount to add to reserves")
    async def fund_house(self, interaction: discord.Interaction, amount_sol: str) -> None:
        await self._guard(interaction)
        treasury = await self._container.admin.fund_house(
            SolAmount.parse(amount_sol),
            str(interaction.user.id),
        )
        await interaction.response.send_message(
            **self._embeds.house(
                treasury.sol_balance_lamports,
                treasury.total_fees_collected_lamports,
            ).send_kwargs(ephemeral=True)
        )

    @admin.command(name="config", description="Set lobby, outcomes, categories, PF, and admin alert channels")
    async def config(self, interaction: discord.Interaction) -> None:
        await self._guard(interaction)
        if interaction.guild_id is None:
            await interaction.response.send_message(
                **self._embeds.error("Use this command in a server.").send_kwargs(ephemeral=True)
            )
            return
        config = await self._container.guild_configs.get(str(interaction.guild_id))
        panel = build_admin_config(self._container, self._embeds, config)
        await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))

    @admin.command(name="post", description="Send casino embeds to a channel")
    @app_commands.describe(
        embed="Which embed to send",
        channel="Destination channel. Leave empty to use configured channels or this channel.",
    )
    @app_commands.choices(
        embed=[
            app_commands.Choice(name="How to play", value="howto"),
            app_commands.Choice(name="Coinflip how to play", value="coinflip"),
            app_commands.Choice(name="Open coinflips header", value="open_coinflips"),
            app_commands.Choice(name="Open dice header", value="open_dice"),
            app_commands.Choice(name="Coinflip outcomes header", value="coinflip_outcomes"),
            app_commands.Choice(name="Dice header", value="dice"),
            app_commands.Choice(name="Dice outcomes header", value="dice_outcomes"),
            app_commands.Choice(name="Provably fair panel", value="provably_fair"),
            app_commands.Choice(name="Verify panel", value="verify"),
            app_commands.Choice(name="Deposit alerts header", value="deposits"),
            app_commands.Choice(name="Withdrawal alerts header", value="withdrawals"),
            app_commands.Choice(name="House ledger header", value="house_ledger"),
            app_commands.Choice(name="All configured channels", value="all_configured"),
        ]
    )
    async def post(
        self,
        interaction: discord.Interaction,
        embed: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._guard(interaction)
        if interaction.guild is None:
            await interaction.response.send_message(
                **self._embeds.error("Use this command in a server.").send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.defer(ephemeral=True)
        here = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        posted = await self._post_embeds(interaction.guild, embed.value, channel, here)
        if not posted:
            await interaction.followup.send(
                **self._embeds.error(
                    "Nothing was posted. Set channels in `/admin config` or pass a text channel."
                ).send_kwargs(ephemeral=True)
            )
            return
        await interaction.followup.send(
            **self._embeds.success("Posted", "\n".join(posted)).send_kwargs(ephemeral=True)
        )

    async def _post_embeds(
        self,
        guild: discord.Guild,
        kind: str,
        explicit: discord.TextChannel | None,
        fallback: discord.TextChannel | None,
    ) -> list[str]:
        config = await self._container.guild_configs.get(str(guild.id))
        chosen = str(explicit.id) if explicit else None
        here = str(fallback.id) if fallback else None
        jobs: list[tuple[str | None, CasinoPanel]] = []
        if kind == "all_configured":
            jobs = [
                (config.listing_channel_id(GameType.COINFLIP), self._embeds.channel_open_coinflips()),
                (config.listing_channel_id(GameType.DICE), self._embeds.channel_open_dice()),
                (config.coinflip_outcomes_channel_id, self._embeds.channel_outcomes("coinflip")),
                (config.dice_outcomes_channel_id, self._embeds.channel_outcomes("dice")),
                (config.deposit_alerts_channel_id, self._embeds.channel_deposit_alerts()),
                (config.withdrawal_alerts_channel_id, self._embeds.channel_withdrawal_alerts()),
                (config.house_ledger_channel_id, self._embeds.channel_house_ledger()),
            ]
        elif kind == "howto":
            jobs = [(chosen or here, self._embeds.howto())]
        elif kind == "coinflip":
            jobs = [(chosen or here, self._embeds.channel_coinflip())]
        elif kind == "open_coinflips":
            jobs = [(chosen or config.listing_channel_id(GameType.COINFLIP), self._embeds.channel_open_coinflips())]
        elif kind == "open_dice":
            jobs = [(chosen or config.listing_channel_id(GameType.DICE), self._embeds.channel_open_dice())]
        elif kind == "coinflip_outcomes":
            jobs = [(chosen or config.coinflip_outcomes_channel_id, self._embeds.channel_outcomes("coinflip"))]
        elif kind == "dice":
            jobs = [(chosen or here, self._embeds.channel_dice())]
        elif kind == "dice_outcomes":
            jobs = [(chosen or config.dice_outcomes_channel_id, self._embeds.channel_outcomes("dice"))]
        elif kind == "deposits":
            jobs = [(chosen or config.deposit_alerts_channel_id, self._embeds.channel_deposit_alerts())]
        elif kind == "withdrawals":
            jobs = [(chosen or config.withdrawal_alerts_channel_id, self._embeds.channel_withdrawal_alerts())]
        elif kind == "house_ledger":
            jobs = [(chosen or config.house_ledger_channel_id, self._embeds.channel_house_ledger())]

        posted: list[str] = []
        for channel_id, payload in jobs:
            if not channel_id:
                continue
            destination = guild.get_channel(int(channel_id))
            if not isinstance(destination, discord.TextChannel):
                continue
            try:
                await destination.send(**payload.send_kwargs())
                payload.stop()
            except discord.HTTPException:
                continue
            posted.append(f"{payload.title} → {destination.mention}")
        if kind in {"provably_fair", "all_configured"}:
            pf_line = await self._post_provably_fair(guild, chosen if kind == "provably_fair" else None)
            if pf_line:
                posted.append(pf_line)
        if kind in {"verify", "all_configured"}:
            verify_line = await self._post_verify(guild, chosen if kind == "verify" else None)
            if verify_line:
                posted.append(verify_line)
        return posted

    async def _post_provably_fair(self, guild: discord.Guild, override_channel_id: str | None) -> str | None:
        from casino.discord_bot.bot import CasinoBot

        if override_channel_id:
            config = await self._container.guild_configs.get(str(guild.id))
            if config.provably_fair_channel_id != override_channel_id:
                await self._container.guild_configs.set_slot(
                    str(guild.id), GuildConfigSlot.PROVABLY_FAIR, override_channel_id
                )
        if not isinstance(self.bot, CasinoBot):
            return None
        message = await self.bot.tables.ensure_provably_fair_panel(str(guild.id))
        if message is None:
            return None
        channel = message.channel
        mention = channel.mention if isinstance(channel, discord.TextChannel) else "provably fair"
        return f"Provably fair → {mention}"

    async def _post_verify(self, guild: discord.Guild, override_channel_id: str | None) -> str | None:
        from casino.discord_bot.cogs.verification import VerificationCog

        if override_channel_id:
            config = await self._container.guild_configs.get(str(guild.id))
            if config.verify_channel_id != override_channel_id:
                await self._container.guild_configs.set_slot(
                    str(guild.id), GuildConfigSlot.VERIFY, override_channel_id
                )
        cog = self.bot.get_cog("VerificationCog")
        if not isinstance(cog, VerificationCog):
            return None
        message = await cog.ensure_panel(str(guild.id))
        if message is None:
            return None
        channel = message.channel
        mention = channel.mention if isinstance(channel, discord.TextChannel) else "verify"
        return f"Verify → {mention}"

    @admin.command(name="grant-role", description="Grant founder or moderator")
    @app_commands.describe(member="Player", role="founder or moderator")
    @app_commands.choices(
        role=[
            app_commands.Choice(name="founder", value="founder"),
            app_commands.Choice(name="moderator", value="moderator"),
        ]
    )
    async def grant_role(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: app_commands.Choice[str],
    ) -> None:
        await self._guard(interaction)
        updated = await self._container.admin.grant_role(str(member.id), UserRole(role.value))
        await interaction.response.send_message(
            **self._embeds.success(
                "Role granted",
                f"{member.mention} roles: {', '.join(r.value for r in updated.roles)}",
            ).send_kwargs(ephemeral=True)
        )
